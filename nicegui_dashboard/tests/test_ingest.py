"""Exercise the handoff and the original registry with its small, labelled QA bundles."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import sys
import tempfile
import unittest

from nicegui_dashboard.ingest import publish, publish_group

SERVICE_PROJECT = Path(__file__).resolve().parents[2] / "result_service"
sys.path.insert(0, str(SERVICE_PROJECT))
from dashboard_service.registry import Registry
from dashboard_tests.fixtures import make_bundle, seal


class ProducerHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nicegui-handoff-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "producer"
        self.store = Registry(SERVICE_PROJECT, self.root / "destination", scan_raw=False)
        self.addCleanup(self.store.close)

    def test_two_completions_retry_revision_and_restart(self):
        first, original = make_bundle(self.source, completed_at="2026-11-21T05:20:00+00:00")
        second, _ = make_bundle(self.source, 2, completed_at=None)
        with contextlib.redirect_stdout(io.StringIO()), ThreadPoolExecutor(max_workers=3) as pool:
            outcomes = sorted(row["outcome"] for row in pool.map(lambda path: publish(path, self.store),
                                                                 [first, second, first]))
        self.assertEqual(outcomes, ["already_published", "published", "published"])
        self.store.scan_once()
        self.assertEqual(self.store.generation, 2)
        self.store.scan_once()
        self.assertEqual(self.store.generation, 2)
        revision, _ = make_bundle(self.source, revision="synthetic_revision_0002",
                                  completed_at="2026-11-21T05:20:00+00:00")
        with contextlib.redirect_stdout(io.StringIO()):
            publish(revision, self.store)
        restarted = Registry(SERVICE_PROJECT, self.store.output, scan_raw=False)
        self.addCleanup(restarted.close)
        restarted.scan_once()
        catalog = restarted.list()
        self.assertEqual(catalog["generation"], 3)
        rows = [r for r in catalog["runs"] if r["run_id"] == original["run_id"]]
        self.assertEqual({r["bundle_revision"] for r in rows}, {first.name, revision.name})
        self.assertTrue(any(r["completed_at"] is None for r in catalog["runs"]))

    def test_incomplete_unsupported_corrupt_and_quota_are_never_listed(self):
        incomplete, _ = make_bundle(self.source, 1)
        (incomplete / "READY").unlink()
        unsupported, invalid = make_bundle(self.source, 2)
        invalid["schema_version"] = "8.0.0"
        seal(unsupported, invalid)
        corrupt, _ = make_bundle(self.source, 3)
        (corrupt / "data.sqlite").write_bytes(b"damaged")
        for source in (incomplete, unsupported, corrupt):
            with self.subTest(source=source), self.assertRaises((ValueError, OSError)):
                publish(source, self.store)
        complete, _ = make_bundle(self.source, 4)
        self.store.max_output_bytes = 1
        with self.assertRaisesRegex(ValueError, "storage_quota_exceeded"):
            publish(complete, self.store)
        self.assertTrue((complete / "READY").is_file())
        self.store.scan_once()
        self.assertEqual(self.store.list()["runs"], [])
        self.assertEqual(list((self.store.output / "staging").glob("handoff-*")), [])

    def test_changed_revision_cannot_overwrite_accepted_bytes(self):
        path, data = make_bundle(self.source, 1)
        with contextlib.redirect_stdout(io.StringIO()):
            publish(path, self.store)
        self.store.scan_once()
        target = self.store.output / "bundles" / path.parent.name / path.name / "manifest.json"
        original = target.read_bytes()
        data["optional_new_field"] = "modified"
        seal(path, data)
        with self.assertRaisesRegex(ValueError, "different content"):
            publish(path, self.store)
        self.assertEqual(original, target.read_bytes())
        self.store.scan_once()
        self.assertEqual(self.store.generation, 1)

    def test_pair_is_not_partially_visible_when_second_arm_exceeds_budget(self):
        first, _ = make_bundle(self.source, 1)
        second, _ = make_bundle(self.source, 2)
        self.store.max_output_bytes = self.store._output_usage() + sum(p.stat().st_size for p in first.iterdir()) + 1024
        with self.assertRaisesRegex(ValueError, "storage_quota_exceeded"):
            publish_group([first, second], self.store)
        self.assertEqual(list((self.store.output / "bundles").glob("*/*/READY")), [])

    def test_completed_only_scan_releases_writer_lock_between_bundles(self):
        from unittest.mock import patch
        first, _ = make_bundle(self.source, 1)
        second, _ = make_bundle(self.source, 2)
        publish_group([first, second], self.store)
        other = Registry(SERVICE_PROJECT, self.store.output, scan_raw=False)
        self.addCleanup(other.close)
        released = []
        def between_bundles(_):
            with other._output_scan_lock() as acquired:
                released.append(acquired)
        with patch("dashboard_service.registry.time.sleep", between_bundles):
            self.assertTrue(self.store.scan_once())
        self.assertEqual(released, [True, True])
        self.assertEqual(len(self.store.list()["runs"]), 2)

    def test_restart_reuses_full_validation_only_for_identical_artifacts(self):
        from unittest.mock import patch
        from dashboard_contract.validation import validate_bundle
        first, _ = make_bundle(self.source, 1)
        publish(first, self.store)
        self.store.scan_once()
        restarted = Registry(SERVICE_PROJECT, self.store.output, scan_raw=False)
        self.addCleanup(restarted.close)
        with patch("dashboard_service.registry.validate_bundle", wraps=validate_bundle) as validate:
            restarted.scan_once()
            self.assertEqual(len(restarted.list()["runs"]), 1)
            validate.assert_not_called()
        db = self.store.output/"bundles"/first.parent.name/first.name/"data.sqlite"
        original = db.read_bytes()
        db.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with patch("dashboard_service.registry.validate_bundle", wraps=validate_bundle) as validate:
            restarted.scan_once()
            validate.assert_called_once()
            self.assertEqual(restarted.list()["runs"], [])


if __name__ == "__main__":
    unittest.main()
