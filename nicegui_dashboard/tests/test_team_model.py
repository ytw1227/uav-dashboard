import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nicegui_dashboard.team_model import FORECAST_REPORT, RELEASE, model_for_flight, verified_profile


class TeamModelTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        selected = patch("nicegui_dashboard.team_model.source_path", return_value=self.repo)
        selected.start()
        self.addCleanup(selected.stop)
        self.git("init", "-q")
        sha = "a" * 64
        self.meta = {"controller": "testing-05", "decision_interval_s": .5,
                     "step": 30, "threshold": .62, "sha256": sha}
        self.flight = {"model_sha256": sha, "controller": "testing-05", "decision_interval_s": .5,
                       "run_id": "team-test-flight", "bundle_revision": "rev-testing"}
        self.write(RELEASE + "checkpoint.json", self.meta)
        checks = {}
        for key in ("release.py", "mission.py", "validation.py"):
            data = (key + "\n").encode()
            (self.repo / RELEASE / key).write_bytes(data)
            checks[key] = hashlib.sha256(data).hexdigest()
        self.group = {"maps": 3, "after": {"flights": 4, "completed": 3, "starts_per_h": 4.5,
                                           "timely_pdr": .91, "cut_seconds_per_h": 22., "relay_m": 50.}}
        self.summary = {"controller": "testing-05", "checkpoint": self.meta, "source_checksums": checks,
                        "separate_maps": self.group, "completed_at": "2026-11-22"}
        self.write(RELEASE + "validation_summary.json", self.summary)
        self.scores = dict(recall=.5, precision=.6, false_per_h=0., average_precision=.64, events=10)
        self.comparison = {"selected": {"sha256": sha, "step": 30},
                           "forecast_operating": {"joint": self.scores}}
        self.write(FORECAST_REPORT, self.comparison)
        self.flight["team_commit"] = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), "-c", "core.autocrlf=false",
                                      "-c", "user.name=Test", "-c", "user.email=test@example.test", *args]).strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "model release")
        return self.git("rev-parse", "HEAD").decode()

    def write(self, path, value):
        target = self.repo / path
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes(json.dumps(value).encode())

    def test_reads_selected_commit_after_next_sync_and_survives_detached_source(self):
        self.group["after"]["starts_per_h"] = 7.25
        self.write(RELEASE + "validation_summary.json", self.summary)
        newer = {**self.flight, "team_commit": self.commit()}
        first, second = [model_for_flight(f)["profile"] for f in (self.flight, newer)]
        self.assertEqual(first["controls"]["separate_maps"]["starts_per_h"], 4.5)
        self.assertEqual(second["controls"]["separate_maps"]["starts_per_h"], 7.25)
        self.assertEqual(first["forecast"]["false_per_h"], 0)
        with patch("nicegui_dashboard.team_model.verified_profile", side_effect=AssertionError("should use sealed data")):
            self.assertEqual(model_for_flight({**self.flight, "team_model_profile": first})["profile"], first)
        self.assertEqual(model_for_flight({**newer, "team_model_profile": first})["profile"], second)

    def test_suppresses_wrong_source_weights_threshold_and_unrelated_forecast_checkpoint(self):
        self.assertIsNone(verified_profile({**self.flight, "model_sha256": "c" * 64}))
        self.assertIsNone(verified_profile({**self.flight, "model_threshold": .71}))
        self.comparison["selected"] = {"step": 60, "sha256": "f" * 64}
        self.write(FORECAST_REPORT, self.comparison)
        self.write(RELEASE + "mission.py", "new navigation")
        updated = verified_profile({**self.flight, "team_commit": self.commit()})
        self.assertEqual(updated["controls"], {})
        self.assertEqual(updated["forecast"], {})
        self.assertIn("control_reason", updated)
        self.assertIn("forecast_reason", updated)

    def test_release_evaluation_is_preferred_when_bundled_with_future_checkpoint(self):
        self.summary["forecast_operating"] = {"joint": {**self.scores, "recall": .8}}
        self.write(RELEASE + "validation_summary.json", self.summary)
        model = verified_profile({**self.flight, "team_commit": self.commit()})
        self.assertEqual(model["forecast"]["recall"], .8)
        self.assertNotIn(FORECAST_REPORT, model["source_refs"])
