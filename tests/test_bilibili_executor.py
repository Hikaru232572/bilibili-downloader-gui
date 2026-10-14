from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from bili.auth import AuthService
from bili.bilibili import BilibiliExecutor,DownloadTools
from bili.files import OutputFiles
from bili.models import Component,OutputSpec,Platform,TaskSpec,TaskState
from bili.secrets import SecretVault
from bili.storage import TaskStore
from bili.tasks import TaskService
from bili.validation import validate_media


ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name=='nt','Windows desktop executor integration')
class BilibiliExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_directory=tempfile.TemporaryDirectory()
        cls.media=Path(cls.media_directory.name)/'fixture.mp4'
        subprocess.run([str(ROOT/'bin/ffmpeg.exe'),'-y','-f','lavfi','-i','testsrc=size=160x90:rate=10',
                        '-f','lavfi','-i','sine=frequency=440','-t','1','-c:v','mpeg4','-c:a','aac',
                        str(cls.media)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)

    @classmethod
    def tearDownClass(cls):
        cls.media_directory.cleanup()

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)
        self.store=TaskStore(self.root/'state/tasks.sqlite3')
        self.vault=SecretVault(self.root/'state/secrets')
        self.auth=AuthService(self.store)
        self.script=self.root/'fake_cli.py'
        self.script.write_text('''import pathlib,shutil,sys,time
args=sys.argv
root=pathlib.Path(args[args.index('--paths')+1])
template=args[args.index('--output')+1]
url=sys.stdin.read().strip()
(root/'stdin-record.txt').write_text(url,encoding='utf-8')
if 'slow' in url:
 print('[download] 10.0%',flush=True)
 time.sleep(60)
elif 'missing' not in url:
 destination=root/template.replace('%(ext)s','mp4')
 if not destination.exists():shutil.copyfile(args[1],destination)
print('[download] 100.0%',flush=True)
''',encoding='utf-8')
        self.commands=[]
        def builder(spec,tools,auth,directory,url):
            command=[sys.executable,str(self.script),str(self.media),'--paths',str(directory),
                     '--output','old-template.%(ext)s',url]
            self.commands.append(command)
            return command
        self.executor=BilibiliExecutor(self.store,self.vault,self.auth,
            DownloadTools(Path(sys.executable),Path('aria2'),ROOT/'bin/ffmpeg.exe',ROOT/'bin/ffprobe.exe'),builder)
        self.service=TaskService(self.store,self.executor)

    def tearDown(self):
        self.service.close()
        self.store.close()
        self.directory.cleanup()

    def submit(self,work='normal'):
        source=self.vault.put('https://www.bilibili.com/video/'+work)
        spec=TaskSpec(Platform.BILIBILI,work,'中文作品',source,str(self.root),('video',),(Component.VIDEO,))
        output=OutputSpec(spec.task_id,'video',Component.VIDEO,1)
        self.service.submit(spec,(output,))
        return spec,output

    def wait(self,condition):
        end=time.monotonic()+8
        while not condition():
            if time.monotonic()>end:self.fail('executor timed out')
            time.sleep(.02)

    def test_success_validates_real_media_and_source_is_not_in_cli_arguments(self):
        spec,output=self.submit()
        self.wait(lambda:self.store.task(spec.task_id).state in {TaskState.SUCCEEDED,TaskState.FAILED})
        record=self.store.task(spec.task_id)
        self.assertEqual(record.state,TaskState.SUCCEEDED,record.error_code)
        result,=self.store.outputs(spec.task_id)
        final=Path(result['path'])
        self.assertTrue(final.is_file())
        self.assertTrue(final.name.startswith('B站_中文作品_normal_001_video'))
        self.assertEqual(final.read_bytes(),self.media.read_bytes())
        self.assertNotIn('https://', ' '.join(self.commands[0]))
        self.assertEqual((self.root/'stdin-record.txt').read_text(),self.vault.get(spec.source_ref))

    def test_zero_exit_without_media_is_failed(self):
        spec,output=self.submit('missing')
        self.wait(lambda:self.store.task(spec.task_id).state==TaskState.FAILED)
        result,=self.store.outputs(spec.task_id)
        self.assertEqual(result['state'],'failed')
        self.assertFalse(any(p.name.startswith('B站_') for p in self.root.iterdir()))

    def test_missing_requested_subtitle_preserves_video_and_records_partial_reason(self):
        spec=TaskSpec(Platform.BILIBILI,'subtitle','字幕样本',
                      self.vault.put('https://www.bilibili.com/video/subtitle'),str(self.root),
                      ('video',),(Component.VIDEO,),subtitle_language='all')
        self.service.submit(spec,(OutputSpec(spec.task_id,'video',Component.VIDEO,1),))
        self.wait(lambda:self.store.task(spec.task_id).state in {TaskState.PARTIAL,TaskState.FAILED})
        record=self.store.task(spec.task_id)
        self.assertEqual(record.state,TaskState.PARTIAL)
        self.assertEqual(record.error_code,'subtitle_missing')
        self.assertIn('字幕',record.message)
        self.assertEqual(self.store.outputs(spec.task_id)[0]['state'],'succeeded')
        old_path=self.store.outputs(spec.task_id)[0]['path']
        self.service.retry(spec.task_id)
        self.wait(lambda:self.store.task(spec.task_id).state in {TaskState.PARTIAL,TaskState.FAILED})
        self.assertEqual(self.store.task(spec.task_id).state,TaskState.PARTIAL)
        self.assertEqual(self.store.outputs(spec.task_id)[0]['path'],old_path)
        self.assertEqual(len(list(self.root.glob('B站_*.mp4'))),1)
        self.assertEqual(len(self.commands),2)

    def test_cancel_stops_owned_cli_and_next_work_runs(self):
        spec,output=self.submit('slow')
        self.wait(lambda:(self.root/'stdin-record.txt').exists())
        next_spec,_=self.submit('next')
        self.service.cancel(spec.task_id)
        self.wait(lambda:self.store.task(next_spec.task_id).state==TaskState.SUCCEEDED)
        self.assertEqual(self.store.task(spec.task_id).state,TaskState.CANCELLED)
        self.assertEqual(self.store.outputs(spec.task_id)[0]['state'],'cancelled')

    def test_exit_during_slow_validation_stops_native_worker_tree(self):
        marker=self.root/'probe-started.txt'
        fake=self.root/'slow-ffprobe.cmd'
        fake.write_text('@echo off\necho started>"'+str(marker)+'"\n"'+sys.executable+
                        '" -c "import time;time.sleep(60)"\n',encoding='utf-8')
        self.executor.tools=DownloadTools(Path(sys.executable),Path('aria2'),ROOT/'bin/ffmpeg.exe',fake)
        spec,_=self.submit('validation')
        self.wait(lambda:marker.exists())
        start=time.monotonic()
        self.service.close()
        self.assertLess(time.monotonic()-start,8)
        self.assertEqual(self.store.task(spec.task_id).state,TaskState.INTERRUPTED)

    def test_crash_after_commit_reconciles_only_registered_validated_file(self):
        self.service.close()
        spec=TaskSpec(Platform.BILIBILI,'crash','crash',self.vault.put('https://www.bilibili.com/video/crash'),
                      str(self.root),('video',),(Component.VIDEO,))
        output=OutputSpec(spec.task_id,'video',Component.VIDEO,1)
        self.store.submit(spec,(output,))
        claimed=self.store.claim_next()
        temporary=self.root/('.bili-'+output.output_id+'.mp4')
        temporary.write_bytes(self.media.read_bytes())
        evidence=validate_media(temporary,Component.VIDEO,ffprobe=ROOT/'bin/ffprobe.exe')
        final=self.root/'registered.mp4'
        self.store.update_output(output.output_id,state='validating',path=str(final),
                                 evidence=evidence,attempt_id=claimed.attempt_id)
        OutputFiles.commit(temporary,final)
        self.store.close()
        self.store=TaskStore(self.root/'state/tasks.sqlite3')
        self.executor.store=self.store
        self.auth.store=self.store
        self.service=TaskService(self.store,self.executor)
        self.service.retry(spec.task_id)
        self.wait(lambda:self.store.task(spec.task_id).state==TaskState.SUCCEEDED)
        self.assertEqual(self.commands,[])
        self.assertEqual(self.store.outputs(spec.task_id)[0]['path'],str(final))
