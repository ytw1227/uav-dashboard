"""Checks for the optional physical-byte quota and lossless staged handoff."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from nicegui_dashboard.ingest import publish_group
from nicegui_dashboard.managed_service import SERVICE_PROJECT

sys.path.insert(0, str(SERVICE_PROJECT))
from dashboard_contract.validation import sha256
from dashboard_service import storage_policy
from dashboard_service.registry import Registry
from dashboard_tests.fixtures import make_bundle, seal


class StoragePolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="uav-storage-policy-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Registry(SERVICE_PROJECT, self.root / "store", scan_raw=False)
        self.addCleanup(self.store.close)

    def enable(self):
        path = self.store.output / storage_policy.POLICY_FILE
        path.write_text(json.dumps(storage_policy.LZX_POLICY), encoding="utf-8")

    def test_policy_is_opt_in_and_compression_failure_does_not_publish_either_arm(self):
        self.assertFalse(storage_policy.compression_enabled(self.store.output))
        self.enable()
        self.assertTrue(storage_policy.compression_enabled(self.store.output))
        first, _ = make_bundle(self.root / "source", 1)
        second, _ = make_bundle(self.root / "source", 2)
        with patch.object(storage_policy, "compress_staged_sqlite", side_effect=[None,OSError("unavailable")]):
            with self.assertRaisesRegex(OSError, "unavailable"):
                publish_group([first, second], self.store)
        self.assertFalse(list((self.store.output / "bundles").glob("*/*/READY")))
        self.assertTrue(all((p / "READY").is_file() for p in (first, second)))

    @unittest.skipUnless(os.name == "nt", "Win32 compressed size semantics")
    def test_invalid_file_size_uses_last_error_and_handles_valid_large_sizes(self):
        import ctypes

        source = self.root / "sample"
        source.write_bytes(b"abc")
        def unavailable(_path, _high):
            ctypes.set_last_error(5)
            return 0xffffffff
        def valid(_path, high):
            ctypes.cast(high, ctypes.POINTER(ctypes.wintypes.DWORD))[0] = 2
            return 0xffffffff
        with patch.object(storage_policy, "_compressed_file_size_api", return_value=unavailable):
            self.assertEqual(storage_policy.allocated_bytes(source), 3)
        with patch.object(storage_policy, "_compressed_file_size_api", return_value=valid):
            self.assertEqual(storage_policy.allocated_bytes(source), (2 << 32) | 0xffffffff)

    @unittest.skipUnless(os.name == "nt" and os.environ.get("UAV_TEST_LZX") == "1", "requires Windows WOF access")
    def test_real_staged_lzx_under_disk_quota_preserves_source_and_registered_bytes(self):
        self.enable()
        sample, manifest = make_bundle(self.root / "source")
        database = sample / "data.sqlite"
        connection = sqlite3.connect(database)
        try:
            with connection:
                connection.execute("CREATE TABLE padding (content BLOB)")
                connection.execute("INSERT INTO padding VALUES (zeroblob(2097152))")
        finally:
            connection.close()
        seal(sample, manifest)
        checksum = sha256(database)
        limit = self.store._output_usage() + 256 * 1024
        self.store.max_output_bytes = limit
        self.assertGreater(database.stat().st_size, limit)
        result, = publish_group([sample], self.store)
        self.assertEqual(result["outcome"], "published")
        destination = self.store.output / "bundles" / manifest["run_id"] / manifest["bundle_revision"] / "data.sqlite"
        self.assertEqual(sha256(destination), checksum)
        self.assertEqual(sha256(database), checksum)
        self.assertLess(storage_policy.allocated_bytes(destination), database.stat().st_size)
        self.assertLessEqual(self.store._output_usage(), limit)
