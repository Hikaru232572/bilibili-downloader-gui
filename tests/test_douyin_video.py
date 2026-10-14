import asyncio
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import AsyncMock
from unittest.mock import patch

from bili.auth import AuthService,Credentials
from bili.douyin_video import DouyinVideoExecutor
from bili.gallery import output_plan,protect_work
from bili.models import Candidate,Component,MediaItem,Platform,TaskSpec,TaskState,Work
from bili.secrets import SecretVault
from bili.storage import TaskStore
from bili.tasks import TaskService
from bili.validation import validate_media
from tests.test_gallery import TestProtector


ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name=='nt','Windows media integration')
class DouyinVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir=tempfile.TemporaryDirectory()
        cls.fixture=Path(cls.fixture_dir.name)/'video.mp4'
        subprocess.run([str(ROOT/'bin/ffmpeg.exe'),'-y','-f','lavfi','-i','testsrc=size=160x90:rate=10',
            '-f','lavfi','-i','sine=frequency=440','-t','1','-c:v','mpeg4','-c:a','aac',str(cls.fixture)],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)

    @classmethod
    def tearDownClass(cls):cls.fixture_dir.cleanup()

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)
        self.store=TaskStore(self.root/'state/tasks.sqlite3')
        self.vault=SecretVault(self.root/'state/secrets',TestProtector())
        self.auth=AuthService(self.store)
        self.parser=type('Parser',(),{})()
        self.parser.authenticate=AsyncMock(return_value=Credentials(Platform.DOUYIN,0,CookieJar()))
        self.downloads=0
        async def download(candidate,platform,temporary,**kwargs):
            self.downloads+=1
            value=self.fixture.read_bytes();Path(temporary).write_bytes(value)
            await kwargs['progress'](len(value),len(value))
            return {'bytes':len(value)}
        transfer=type('Transfer',(),{'download':staticmethod(download)})()
        self.executor=DouyinVideoExecutor(self.store,self.vault,self.auth,self.parser,transfer=transfer,
            ffprobe=ROOT/'bin/ffprobe.exe',ffmpeg=ROOT/'bin/ffmpeg.exe')
        self.service=TaskService(self.store,self.executor)

    def tearDown(self):
        self.service.close();self.store.close();self.directory.cleanup()

    def submit(self,components=(Component.VIDEO,Component.AUDIO),policy='original'):
        media=Work(Platform.DOUYIN,'123','video',(MediaItem('123:video',1,'video',
            video_candidates=(Candidate('https://v.douyinvod.com/fixture',Component.VIDEO,'test'),)),),title='中文视频')
        spec=TaskSpec(media.platform,media.work_id,media.title,self.vault.put('https://www.douyin.com/video/123'),
            str(self.root),('123:video',),components,audio_format=policy,work_ref=protect_work(self.vault,media))
        self.service.submit(spec,output_plan(spec,media))
        return spec

    def finish(self,spec):
        end=time.monotonic()+15
        while self.store.task(spec.task_id).state not in (TaskState.SUCCEEDED,TaskState.PARTIAL,TaskState.FAILED,TaskState.CANCELLED):
            if time.monotonic()>end:self.fail('video execution timed out')
            time.sleep(.02)
        return self.store.task(spec.task_id)

    def test_both_outputs_share_one_download_and_audio_keeps_codec(self):
        spec=self.submit()
        self.assertEqual(self.finish(spec).state,TaskState.SUCCEEDED)
        rows=self.store.outputs(spec.task_id)
        audio=next(row for row in rows if row['component']=='audio')
        evidence=json.loads(audio['evidence'])
        self.assertTrue(audio['path'].endswith('.m4a'))
        self.assertEqual(evidence['audio_policy'],'stream_copy')
        self.assertEqual(evidence['streams'][0]['codec_name'],'aac')
        self.assertEqual(self.downloads,1)

    def test_audio_only_uses_complete_video_track_and_removes_temporary_source(self):
        spec=self.submit((Component.AUDIO,))
        self.assertEqual(self.finish(spec).state,TaskState.SUCCEEDED)
        self.assertEqual(len(self.store.outputs(spec.task_id)),1)
        self.assertFalse(list(self.root.glob('*.mp4')))
        self.assertFalse(list(self.root.glob('.bili-*.part')))

    def test_explicit_mp3_and_wav_are_real_transcodes(self):
        for policy,codec in [('mp3','mp3'),('wav','pcm_s16le')]:
            spec=self.submit((Component.AUDIO,),policy)
            self.assertEqual(self.finish(spec).state,TaskState.SUCCEEDED)
            row,=self.store.outputs(spec.task_id)
            self.assertTrue(row['path'].endswith('.'+policy))
            evidence=json.loads(row['evidence'])
            self.assertEqual(evidence['streams'][0]['codec_name'],codec)
            self.assertEqual(evidence['audio_policy'],'transcoded_'+policy)

    def test_failed_audio_retry_reuses_existing_verified_video(self):
        ffmpeg=self.executor.ffmpeg
        self.executor.ffmpeg=None
        spec=self.submit()
        self.assertEqual(self.finish(spec).state,TaskState.PARTIAL)
        video=next(row for row in self.store.outputs(spec.task_id) if row['component']=='video')
        self.executor.ffmpeg=ffmpeg
        self.service.retry(spec.task_id)
        self.assertEqual(self.finish(spec).state,TaskState.SUCCEEDED)
        self.assertEqual(self.downloads,1)
        self.assertEqual(next(row['path'] for row in self.store.outputs(spec.task_id) if row['component']=='video'),video['path'])

    def test_cancel_during_audio_extraction_keeps_video(self):
        entered=__import__('threading').Event()
        async def slow(*args):
            entered.set();await asyncio.sleep(60)
        self.executor._extract_audio=slow
        spec=self.submit()
        self.assertTrue(entered.wait(8))
        self.service.cancel(spec.task_id)
        self.assertEqual(self.finish(spec).state,TaskState.CANCELLED)
        video=next(row for row in self.store.outputs(spec.task_id) if row['component']=='video')
        self.assertEqual(video['state'],'succeeded')
        self.assertTrue(Path(video['path']).is_file())

    def test_playlist_response_cannot_trigger_secondary_network_reads(self):
        for body in (b'#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1/private.ts\n',
                     b'ffconcat version 1.0\nfile http://127.0.0.1/private\n'):
            path=self.root/'response.part';path.write_bytes(body)
            with patch('bili.validation.subprocess.run') as probe:
                with self.assertRaises(ValueError) as error:
                    validate_media(path,Component.VIDEO,ffprobe=ROOT/'bin/ffprobe.exe')
                self.assertEqual(str(error.exception),'non_media_response')
                probe.assert_not_called()


if __name__=='__main__':unittest.main()
