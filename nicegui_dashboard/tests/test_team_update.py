"""Exercise two real local Git revisions, the durable queue, and per-region promotion."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nicegui_dashboard import team_source
from nicegui_dashboard import team_runtime_child
from nicegui_dashboard.team_runtime import map_coverage, prepare_version, research_runtime
from nicegui_dashboard.team_source import source_status, sync_source
from nicegui_dashboard.team_update import TeamUpdates


def git(cwd, *args):
    answer = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=cwd,
                            capture_output=True, encoding="utf-8", errors="replace")
    if answer.returncode:
        raise AssertionError(answer.stderr or answer.stdout)
    return answer.stdout.strip()


def seal_manifest(directory, manifest):
    raw = json.dumps(manifest).encode()
    (directory / "manifest.json").write_bytes(raw)
    (directory / "READY").write_text(json.dumps({"manifest_sha256":hashlib.sha256(raw).hexdigest()}))


class TeamUpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        bare, dev = base / "upstream.git", base / "developer"
        git(base, "init", "--bare", "--initial-branch=main", str(bare))
        git(base, "clone", str(bare), str(dev))
        git(dev, "config", "user.email", "dashboard@example.invalid")
        git(dev, "config", "user.name", "Test author")
        self.dev, self.checkout = dev, base / "team"
        (dev / "fast_halfsec").mkdir()
        (dev / "fast_halfsec/context.py").write_text(
            "def survey_for(entry):\n    if entry['study_role'] not in ('train', 'confirmation'):\n        raise ValueError()\n")
        git(dev, "add", "fast_halfsec/context.py")
        self.commit("A")
        self.enterContext(patch.object(team_source, "TEAM_URL", str(bare)))
        self.first = sync_source(self.checkout)["commit"]
        core = base / "assets"
        (core / "code").mkdir(parents=True)
        (core / "code/run_latest_final_v1.py").write_text("# launcher")
        (core / "code/team_portability.py").write_text("# assets helper")
        (core / "code/model.bin").write_bytes(b"shared model")
        (core / "code/stale_code.py").write_text("stale legacy source")
        (core / "dashboard_execution").mkdir()
        h = hashlib.sha256(b"test scene").hexdigest()
        selected = [{"key":k, "sha256":h} for k in ("one", "two", "protected", "missing")]
        (core / "dashboard_execution/selected_maps.json").write_text(json.dumps({"maps": selected}))
        maps = core / "code/fast_halfsec/artifacts"
        maps.mkdir(parents=True)
        records = [dict(row, path="fixture.scene", study_role="legacy_protected" if row["key"] == "protected" else "train")
                   for row in selected if row["key"] != "missing"]
        (maps / "map_registry.json").write_text(json.dumps({"maps": records}))
        maps = core / "code/drone_forecast_joint_training"
        maps.mkdir()
        (maps / "fixture.scene").write_bytes(b"test scene")
        for key, value in {
            "UAV_RESEARCH_CORE": str(core), "UAV_RESEARCH_PYTHON": sys.executable,
            "UAV_TEAM_SOURCE": str(self.checkout)}.items():
            if key in os.environ:
                self.addCleanup(os.environ.__setitem__, key, os.environ[key])
            else:
                self.addCleanup(os.environ.pop, key, None)
            os.environ[key] = value
        self.manager = TeamUpdates(base / "results", base / "cache", resume=False)
        self.enterContext(patch.object(self.manager, "_launch"))

    def commit(self, content):
        (self.dev / "flight.py").write_text(content, encoding="utf-8")
        git(self.dev, "add", "flight.py")
        git(self.dev, "commit", "-m", content)
        git(self.dev, "push", "origin", "main")
        return git(self.dev, "rev-parse", "HEAD")

    def fake_record(self, job, code):
        self.assertFalse((code / "stale_code.py").exists())
        self.assertTrue((code / "run_latest_final_v1.py").is_file())
        self.assertEqual((code / "model.bin").read_bytes(), b"shared model")
        tag = (code / "flight.py").read_text(encoding="utf-8")
        payload = {"run_id": "run-" + job["region"], "revision": "test-" + tag}
        dest = self.manager.output_root / "bundles" / payload["run_id"] / payload["revision"]
        dest.mkdir(parents=True, exist_ok=True)
        artifacts = {"scene.json": {"size_bytes": 2}, "data.sqlite": {"size_bytes": len(tag)}}
        (dest / "scene.json").write_bytes(b"{}")
        (dest / "data.sqlite").write_bytes(tag.encode())
        seal_manifest(dest, dict(region=job["region"], team_commit=job["sha"], run_id=payload["run_id"],
                                 bundle_revision=payload["revision"], artifacts=artifacts))
        return {"v21": payload}

    def test_source_sync_during_batch_then_selected_upgrade_failure_and_retry(self):
        second = self.commit("B")  # upstream changed; dashboard checkout still points to A
        mgr = self.manager
        mgr.start_many(["one","two","protected","missing"], self.first)
        with self.assertRaisesRegex(ValueError, "진행 중"):
            mgr.start("one", self.first)
        seen = []
        def first_batch(job, code):
            seen.append((job["region"], (code / "flight.py").read_text()))
            if len(seen) == 1:
                self.assertEqual(sync_source(self.checkout)["commit"], second)
            return self.fake_record(job, code)
        with patch.object(mgr, "_capture", side_effect=first_batch):
            mgr.run_worker()
        self.assertEqual(seen, [("one","A"),("two","A")])
        self.assertEqual(mgr.batch()["completed"], 2)
        self.assertEqual(mgr.batch()["unavailable"], 2)
        self.assertIn("legacy_protected", mgr.status("protected")["message"])
        self.assertIsNone(mgr.current("missing"))
        with self.assertRaisesRegex(ValueError, "버전이 바뀌"):
            mgr.start("one", self.first)
        mgr.start("one", second)
        with patch.object(mgr, "_capture", side_effect=RuntimeError("QA flight failure")):
            mgr.run_worker()
        self.assertEqual(mgr.current("one")["sha"], self.first)
        self.assertEqual(mgr.current("two")["sha"], self.first)
        self.assertEqual(mgr.batch()["failed"], 1)
        mgr.retry()
        with patch.object(mgr, "_capture", side_effect=self.fake_record):
            mgr.run_worker()
        self.assertEqual(mgr.current("one")["flights"]["v21"]["revision"], "test-B")
        self.assertEqual(mgr.current("two")["flights"]["v21"]["revision"], "test-A")
        self.assertEqual([r["sha"] for r in mgr.history("one")], [second, self.first])
        with patch.object(mgr, "_launch") as wake:
            mgr.start("one", second)
            wake.assert_not_called()
        self.assertEqual(mgr.status("one")["phase"], "reused")
        self.assertEqual(source_status(self.checkout)["commit"], second)
        self.assertEqual(git(self.checkout, "status", "--porcelain"), "")

    def test_invalid_seal_identity_or_artifact_falls_back_and_is_not_reused(self):
        mgr = self.manager
        mgr.start("one", self.first)
        with patch.object(mgr, "_capture", side_effect=self.fake_record):
            mgr.run_worker()
        self.commit("B")
        second = sync_source(self.checkout)["commit"]
        mgr.start("one", second)
        with patch.object(mgr, "_capture", side_effect=self.fake_record):
            mgr.run_worker()
        bundle = mgr.output_root / "bundles/run-one/test-B"
        manifest = json.loads((bundle / "manifest.json").read_bytes())
        (bundle / "manifest.json").write_text("{}")
        self.assertEqual(mgr.current("one")["sha"], self.first)
        for key in ("run_id", "bundle_revision", "region", "team_commit"):
            with self.subTest(key=key):
                seal_manifest(bundle, {**manifest, key:"another"})
                self.assertEqual([row["sha"] for row in mgr.history("one")], [self.first])
        seal_manifest(bundle, manifest)
        (bundle / "scene.json").write_bytes(b"truncated file")
        self.assertEqual(mgr.current("one")["sha"], self.first)
        (bundle / "scene.json").unlink()
        self.assertEqual(mgr.current("one")["sha"], self.first)
        batch = mgr.start("one", second)
        self.assertEqual(batch["queued"], 1)

    def test_interrupted_item_recoverable_and_remaining_queue_completes(self):
        mgr = self.manager
        mgr.start_many(["one","two"], self.first)
        with mgr.connection() as conn:
            conn.execute("UPDATE jobs SET state='running' WHERE region='one'")
        with patch.object(mgr, "_capture", side_effect=self.fake_record):
            mgr.run_worker()
        self.assertEqual(mgr.status("one")["state"], "completed")
        self.assertEqual(mgr.status("two")["state"], "completed")
        mgr2 = TeamUpdates(mgr.output_root, mgr.state_root, resume=False)
        with patch.object(mgr2, "_capture", side_effect=self.fake_record):
            mgr2.run_worker()
        self.assertEqual(mgr2.current("one")["sha"], self.first)

    def test_recorder_reattach_reads_completed_result_once(self):
        result = self.manager.work / "test-result.json"
        with patch.object(sys, "argv", ["capture", "--result", str(result)]):
            with patch.object(team_runtime_child, "capture_main", side_effect=lambda: result.write_text("{}")) as record:
                self.assertEqual(team_runtime_child.main(), 0)
                self.assertEqual(team_runtime_child.main(), 0)
                record.assert_called_once()

    def test_deleted_registry_cannot_reappear_from_installed_release(self):
        core, _ = research_runtime()
        relative = Path("fast_halfsec/artifacts/map_registry.json")
        target = self.dev / relative
        target.parent.mkdir(parents=True)
        target.write_bytes((core / "code" / relative).read_bytes())
        git(self.dev, "add", relative.as_posix())
        self.commit("owns-registry")
        git(self.dev, "rm", relative.as_posix())
        self.commit("withdraws-registry")
        sha = sync_source(self.checkout)["commit"]
        coverage = map_coverage(self.checkout, sha, core)
        self.assertFalse(coverage["one"]["eligible"])
        staged = prepare_version(self.checkout, sha, core, self.manager.state_root)
        self.assertFalse((staged / relative).exists())
        self.assertTrue((core / "code" / relative).is_file())

    def test_external_weights_must_match_committed_model_manifest(self):
        core, _ = research_runtime()
        folder = Path("final_v1/halfsec_main")
        upstream = self.dev / folder
        upstream.mkdir(parents=True)
        (upstream / "release.py").write_text("# team default")
        new_bytes = b"new official model"
        (upstream / "checkpoint.json").write_text(json.dumps({"sha256":hashlib.sha256(new_bytes).hexdigest()}))
        git(self.dev, "add", folder.as_posix())
        self.commit("new-model")
        sha = sync_source(self.checkout)["commit"]
        weights = core / "code" / folder / "checkpoint.pt"
        weights.parent.mkdir(parents=True)
        weights.write_bytes(b"old team release")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            prepare_version(self.checkout, sha, core, self.manager.state_root)
        self.assertFalse((self.manager.state_root / "versions" / sha).exists())
        weights.write_bytes(new_bytes)
        staged = prepare_version(self.checkout, sha, core, self.manager.state_root)
        self.assertEqual((staged / folder / "checkpoint.pt").read_bytes(), new_bytes)


if __name__ == "__main__":
    unittest.main()
