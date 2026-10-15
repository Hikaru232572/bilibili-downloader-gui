from __future__ import annotations

import os
from contextlib import closing
import json
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from bili.models import Component, Platform, TaskSpec


ROOT = Path(__file__).resolve().parents[1]
DIST = Path(os.environ.get('BILI_PACKAGE_DIR') or ROOT / "dist" / "BilibiliDownloader")
EXE = DIST / "BilibiliDownloader.exe"


class PortablePackageTests(unittest.TestCase):
    def test_portable_executable_exists(self) -> None:
        self.assertTrue(EXE.is_file(), f"Missing packaged executable: {EXE}")

    def test_all_downloader_tools_are_bundled(self) -> None:
        bundled_names = {path.name.lower() for path in DIST.rglob("*.exe")}
        for required in ("yt-dlp.exe", "aria2c.exe", "ffmpeg.exe", "ffprobe.exe"):
            self.assertIn(required, bundled_names)

    def test_packaged_app_initializes_and_exits_cleanly(self) -> None:
        environment = os.environ.copy()
        environment["BILIBILI_DOWNLOADER_SMOKE_TEST"] = "1"
        with tempfile.TemporaryDirectory() as directory:
            environment['BILI_DATA_DIR']=directory
            result = subprocess.run(
                [str(EXE)],
                cwd=DIST,
                env=environment,
                capture_output=True,
                timeout=45,
                check=False,
            )
        self.assertEqual(result.returncode, 0)
        crash_log = DIST / "BilibiliDownloader-crash.log"
        self.assertFalse(crash_log.exists(), crash_log.read_text(encoding="utf-8", errors="replace") if crash_log.exists() else "")

    def test_packaged_restart_migrates_old_success_without_redownloading_or_recreating_history(self):
        environment = os.environ.copy()
        environment['BILIBILI_DOWNLOADER_SMOKE_TEST']='1'
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            environment['BILI_DATA_DIR']=str(directory)
            file = directory/'original-user-file.mp4'
            file.write_bytes(b'user-file-preserved-by-packaged-upgrade')
            spec = TaskSpec(Platform.BILIBILI,'legacy','legacy','protected-ref',str(directory),('item',),(Component.VIDEO,))
            ended = time.time()-60
            database = directory/'tasks.sqlite3'
            with closing(sqlite3.connect(database)) as c, c:
                c.executescript((ROOT/'tests/fixtures/tasks_v1.sql').read_text())
                c.execute("INSERT INTO batches(id,state,created) VALUES('old','ended',?)",(ended-10,))
                c.execute('INSERT INTO tasks(id,submission_key,spec,state,batch_id,created,updated) '
                    "VALUES(?,?,?,'succeeded','old',?,?)",(spec.task_id,spec.submission_key,json.dumps(spec.record()),ended-10,ended))
                c.execute('INSERT INTO outputs(id,task_id,item_id,component,original_index,state,path,evidence) '
                    "VALUES('output',?,'item','video',1,'succeeded',?,?)",(spec.task_id,str(file),json.dumps({'size':file.stat().st_size})))
            for attempt in range(3):
                result = subprocess.run([str(EXE)],cwd=DIST,env=environment,capture_output=True,timeout=45)
                self.assertEqual(result.returncode,0,result.stderr)
                with closing(sqlite3.connect(database)) as c, c:
                    self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],2)
                    self.assertEqual(c.execute('SELECT state,queue_hidden,completed_at FROM tasks').fetchone(),
                                     ('succeeded',1,ended))
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM completion_history').fetchone()[0],1 if attempt==0 else 0)
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],0)
                    c.execute('DELETE FROM completion_history')
                self.assertEqual(file.read_bytes(),b'user-file-preserved-by-packaged-upgrade')
            self.assertTrue(Path(str(database)+'.v1.bak').is_file())


if __name__ == "__main__":
    unittest.main()
