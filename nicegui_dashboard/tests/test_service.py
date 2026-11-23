"""Exercise the bundled results server through the dashboard's normal reader."""
from contextlib import closing
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from nicegui_dashboard.service import ServiceReader, ServiceReadError

SERVICE_PROJECT = Path(__file__).resolve().parents[2] / "result_service"
sys.path.insert(0, str(SERVICE_PROJECT))
from dashboard_service.http import LocalServer
from dashboard_service.registry import Registry
from dashboard_tests.fixtures import make_bundle


class ServiceReaderTests(unittest.TestCase):
    def test_only_loopback_server_roots_are_accepted(self):
        for url in ("https://localhost", "http://example.org", "http://127.0.0.1/api",
                    "http://user@127.0.0.1", "http://localhost/?q=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ServiceReader(url)

    def test_every_run_read_requires_a_pinned_revision(self):
        reader = ServiceReader()
        for operation in (reader.manifest, reader.scene, reader.events):
            with self.subTest(operation=operation.__name__), self.assertRaises(ValueError):
                operation("run-example", "")
        with self.assertRaises(ValueError):
            reader.timeline("run-example", None, start_s=0, end_s=1, at_s=0)

    def test_ready_bundle_through_local_console_api(self):
        with TemporaryDirectory() as output:
            folder, original = make_bundle(Path(output))
            run_id, rev = original["run_id"], original["bundle_revision"]
            with closing(Registry(SERVICE_PROJECT, output, scan_raw=False)) as registry:
                self.assertTrue(registry.scan_once())
                with LocalServer(("127.0.0.1", 0), registry) as server:
                    thread = Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        reader = ServiceReader(f"http://127.0.0.1:{server.server_port}")
                        self.assertTrue(reader.health()["ok"])
                        self.assertFalse(reader.jobs()["capabilities"]["enabled"])
                        records = reader.runs()["runs"]
                        self.assertEqual([(r["run_id"], r["bundle_revision"]) for r in records], [(run_id, rev)])
                        self.assertEqual(reader.manifest(run_id, rev)["final_metrics"], original["final_metrics"])
                        self.assertEqual(reader.scene(run_id, rev), json.loads((folder / "scene.json").read_bytes()))
                        events = reader.events(run_id, rev, start_s=3, end_s=3)["events"]
                        self.assertEqual([e["id"] for e in events], ["outage_fixture"])
                        timeline = reader.timeline(run_id, rev, start_s=2, end_s=4, at_s=3,
                                                   observer="uav-2", peer="uav-0")
                        self.assertEqual(timeline["bundle_revision"], rev)
                        self.assertEqual(timeline["at"]["position_before"]["sample_time_s"], 3)
                        self.assertEqual(timeline["at"]["observations"][0]["available_at_s"], 3)
                        with self.assertRaises(ServiceReadError) as error:
                            reader.manifest(run_id, "unregistered-revision")
                        self.assertEqual(error.exception.code, "run_revision_not_found")
                    finally:
                        server.shutdown()
                        thread.join()
