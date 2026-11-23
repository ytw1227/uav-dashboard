import json
import unittest
from unittest.mock import patch

from nicegui_dashboard.team_bundle import digest, publish_capture
from nicegui_dashboard.team_forecast_backfill import recover_pin
from nicegui_dashboard.team_forecast_record import forecast_path, load_forecast
from nicegui_dashboard.tests import test_team_bundle as fixtures


@unittest.skipUnless(fixtures.np is not None, "Uses the research Python's NumPy")
class TeamForecastBackfillTests(unittest.TestCase):
    def setUp(self):
        flight = fixtures.TeamBundleTests()
        flight.setUp()
        self.addCleanup(flight.doCleanups)
        self.store = flight.root / "store"
        pin = publish_capture(flight.record, flight.a, flight.scene, self.store)
        self.pin = pin["run_id"], pin["revision"]
        self.bundle = self.store / "bundles" / self.pin[0] / self.pin[1]
        self.target = forecast_path(self.store, *self.pin)
        self.capture_dir = flight.root / "native"
        self.capture_dir.mkdir()
        (self.capture_dir / "source.json").write_text(json.dumps({"scene": str(flight.scene)}))
        self.row = dict(flight.record, team_capture_version="2", model_threshold=.4)
        self.arrays = dict(flight.a, fleet_onset_probability=fixtures.np.array([.7, .35]))

    def recover(self):
        (self.capture_dir / "flight.json").write_text(json.dumps(self.row))
        fixtures.np.savez(self.capture_dir / "frames.npz", **self.arrays)
        return recover_pin(self.store, *self.pin, capture_dir=self.capture_dir)

    def test_only_verified_actual_head_attaches_and_sealed_bundle_is_unchanged(self):
        original = {file.name: digest(file) for file in self.bundle.iterdir()}
        self.assertEqual(self.recover(), "recovered")
        forecast = load_forecast(self.target, json.loads((self.bundle / "manifest.json").read_bytes()),
                                 original["manifest.json"])
        self.assertEqual(forecast["probabilities"], [None, None, .35, None])
        self.assertEqual(forecast["pre_action_connected"], [None, True, False, False])
        self.assertEqual(forecast["model_threshold"], .4)
        with patch("nicegui_dashboard.team_forecast_backfill.capture", side_effect=AssertionError):
            self.assertEqual(recover_pin(self.store, *self.pin), "already-recovered")
        self.assertEqual({file.name: digest(file) for file in self.bundle.iterdir()}, original)

    def test_different_trajectory_or_policy_cannot_claim_the_same_forecast(self):
        self.arrays["positions"][1, 0, 0] = 1.23
        with self.assertRaisesRegex(ValueError, "positions"):
            self.recover()
        self.assertFalse(self.target.exists())
        self.arrays["positions"][1, 0, 0] = 0.
        self.arrays["scores"][1] = .9
        with self.assertRaisesRegex(ValueError, "decisions"):
            self.recover()
        self.assertFalse(self.target.exists())

    def test_wrong_checkpoint_or_unsupported_capture_fails_closed(self):
        self.row["model_sha256"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "모델"):
            self.recover()
        self.row["model_sha256"] = "b" * 64
        self.row["max_seconds_requested"] = 2.
        with self.assertRaisesRegex(ValueError, "정식"):
            self.recover()
        self.assertFalse(self.target.exists())
