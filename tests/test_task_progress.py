from queue import Queue
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from bili.bilibili import DOWNLOAD_PROGRESS_TEMPLATE, progress_metrics
from bili.tasks import TaskContext


class TaskProgressTests(unittest.TestCase):
    def test_cli_reports_only_known_numeric_values_and_ignores_estimated_totals(self):
        self.assertEqual(progress_metrics('BILI_PROGRESS:{"downloaded_bytes":50,"total_bytes":100,"speed":20,"filename":"private-path"}'),
                         dict(bytes=50,total=100,speed=20,percent=50))
        self.assertEqual(progress_metrics('BILI_PROGRESS:{"downloaded_bytes":50,"total_bytes_estimate":100}'),
                         dict(bytes=50,total=None,speed=None,percent=None))
        for text in ('raw debug text','BILI_PROGRESS:not-json','BILI_PROGRESS:[]'):
            self.assertIsNone(progress_metrics(text))
        self.assertEqual(progress_metrics('BILI_PROGRESS:{"downloaded_bytes":-1,"total_bytes":"NA","speed":NaN}'),
                         dict(bytes=None,total=None,speed=None,percent=None))

    def test_speed_uses_byte_deltas_and_resets_between_resources_and_retries(self):
        context = TaskContext(Mock(),Queue())
        with patch('bili.tasks.time.monotonic',side_effect=[0,1,2,3,4]):
            context.report_progress(bytes=100,total=1000,output_id='A')
            context.report_progress(bytes=400,total=1000,output_id='A')
            context.report_progress(bytes=50,total=200,output_id='B')
            context.report_progress(bytes=150,total=200,output_id='B')
            context.report_progress(bytes=20,total=200,output_id='B')
        events = [context.events.get_nowait() for _ in range(5)]
        self.assertEqual([e['speed'] for e in events],[None,300,None,100,None])
        self.assertTrue(all(e['percent'] is None for e in events))

    def test_bundled_ytdlp_and_aria2_emit_parseable_real_byte_counts(self):
        root = Path(__file__).resolve().parents[1]
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def copyfile(self,source,outputfile):
                try:
                    super().copyfile(source,outputfile)
                except (ConnectionResetError,BrokenPipeError):
                    # The generic extractor may stop its preliminary request
                    # after identifying the media, before aria2 fetches it.
                    pass
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            payload = b'measured-transfer-fixture'*65536
            (directory/'source.mp4').write_bytes(payload)
            server = ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(directory)))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                destination = directory/'result.mp4'
                result = subprocess.run([str(root/'bin/yt-dlp.exe'),'--ignore-config','--no-playlist',
                    '--newline','--no-warnings','--downloader',str(root/'bin/aria2c.exe'),
                    '--downloader-args','aria2c:--async-dns=false -x 1 -s 1',
                    '--progress-template',DOWNLOAD_PROGRESS_TEMPLATE,
                    '--output',str(destination),f'http://127.0.0.1:{server.server_port}/source.mp4'],
                    capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                self.assertEqual(result.returncode,0,result.stderr)
                measurements = [metrics for line in (result.stdout+result.stderr).splitlines()
                                if (metrics:=progress_metrics(line.strip()))]
                self.assertTrue(measurements,result.stdout+result.stderr)
                self.assertEqual(measurements[-1]['bytes'],len(payload))
                self.assertEqual(measurements[-1]['total'],len(payload))
                self.assertEqual(measurements[-1]['percent'],100)
                self.assertEqual(destination.read_bytes(),payload)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
