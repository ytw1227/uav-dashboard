import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from nicegui_dashboard.team_update import TeamUpdates, UpdateCancelled


class TeamCancellationTests(unittest.TestCase):
    def test_cancel_preserves_completed_and_allows_new_batch_after_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = TeamUpdates(Path(folder)/"results", resume=False)
            with manager.connection() as db:
                bid = db.execute("INSERT INTO batches(sha,source,core,python,added) VALUES(?,?,?,?,?)",
                                 ("a"*40,"source","core",sys.executable,"now")).lastrowid
                for region,state in (("one","completed"),("two","running"),("three","queued")):
                    db.execute("INSERT INTO jobs(batch,region,state,phase,message) VALUES(?,?,?,?,?)",
                               (bid,region,state,state,""))
                job_id = db.execute("SELECT id FROM jobs WHERE region='two'").fetchone()[0]
            job = {"id":job_id,"python":sys.executable,"quota_gib":4}
            timer = threading.Timer(.7, manager.cancel, args=(bid,))
            timer.start()
            began = time.monotonic()
            try:
                with self.assertRaises(UpdateCancelled):
                    manager._run_recorder(job, ["timeit","-n","1","-r","1","import time; time.sleep(60)"])
            finally:
                timer.join()
            self.assertLess(time.monotonic()-began, 12)
            manager._set(job_id,"failed","failed","late failure")
            self.assertEqual(manager.status("two")["state"],"cancelling")
            manager._set(job_id,"cancelled","cancelled","stopped")
            with manager.connection() as db:
                self.assertFalse(manager._active(db))
            self.assertEqual(manager.status("one")["state"],"completed")
            self.assertEqual(manager.status("three")["state"],"cancelled")
            manager.run_worker()
            self.assertEqual(manager.status("two")["state"],"cancelled")


if __name__ == "__main__":
    unittest.main()
