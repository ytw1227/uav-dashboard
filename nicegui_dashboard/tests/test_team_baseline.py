import json
import unittest
from unittest.mock import patch

from result_service.dashboard_contract.validation import canonical_hash
from nicegui_dashboard.team_baseline import capture_pair, recover_pair, store_pair
from nicegui_dashboard.team_baseline_record import comparison_path, load_comparison
from nicegui_dashboard.team_bundle import publish_capture
from nicegui_dashboard.tests import test_team_bundle as fixtures


@unittest.skipUnless(fixtures.np is not None, "Uses the research Python's NumPy")
class TeamBaselineTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.TeamBundleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.row = dict(fixture.record, deployment="s2r1", full_mission_budget_s=5000.,
                        shared_cells=3, target_cells=5, map_input_source="team_registry", source_study_role="test")
        self.nominal = dict(self.row, controller="nominal_no_relocation", shared_cells=2, network_fraction=.75,
                            mission_completed=False, cuts=[[0, 1], [2, 4]], onsets=1, cut_seconds=3., duration_s=4.)
        self.arrays = fixture.a
        self.store = fixture.root / "store"
        self.scene = fixture.scene

    def publish(self, embedded=False):
        pin = publish_capture(self.row, self.arrays, self.scene, self.store,
                              nominal=self.nominal if embedded else None)
        return self.store / "bundles" / pin["run_id"] / pin["revision"]

    def test_embedded_pair_and_original_metric_denominators(self):
        bundle = self.publish(embedded=True)
        pair = load_comparison(bundle)
        self.assertFalse(comparison_path(bundle).exists())
        nominal, applied = pair["nominal"], pair["applied"]
        self.assertEqual(nominal["pair_key"], applied["pair_key"])
        self.assertEqual(nominal["raw_metadata"]["persistent_cut_intervals"], [[0, 1], [2, 4]])
        self.assertFalse(nominal["mission_completed"])
        self.assertTrue(applied["mission_completed"])
        self.assertEqual(applied["final_metrics"]["pdr"], .9)
        self.assertEqual(applied["final_metrics"]["information_shared_fraction"], .6)
        self.assertEqual(nominal["final_metrics"]["information_shared_fraction"], .4)
        self.assertEqual(nominal["metric_definitions"], applied["metric_definitions"])

    def test_wrong_seed_or_mismatched_common_target_set_cannot_be_paired(self):
        bundle = self.publish()
        for changes in (dict(spec={**self.row["spec"], "seed": 2}), dict(model_sha256="c"*64),
                        dict(target_cells=6), dict(controller=self.row["controller"]), dict(shared_cells=-1)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                store_pair(bundle, self.row, {**self.nominal, **changes})
        self.assertIsNone(load_comparison(bundle))

    def test_repeated_recovery_and_tampered_inner_pin(self):
        bundle = self.publish()
        store_pair(bundle, self.row, self.nominal)
        with patch("nicegui_dashboard.team_baseline.capture", side_effect=AssertionError):
            self.assertEqual(recover_pair(self.store, bundle.parent.name, bundle.name), "already-recorded")
        path = comparison_path(bundle)
        original = json.loads(path.read_bytes())
        for changes in (dict(mode="v21"), dict(seed=89), dict(map_hash="x"), dict(pair_key="other/pin")):
            data = {**original, "nominal": {**original["nominal"], **changes}}
            data.pop("payload_sha256")
            data["payload_sha256"] = canonical_hash(data)
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                load_comparison(bundle)

    def test_measured_baseline_must_start_in_same_formation(self):
        nominal = dict(self.row, controller="nominal_no_relocation")
        other = dict(self.arrays, positions=self.arrays["positions"].copy())
        other["positions"][0, 0, 0] += 1
        with patch("nicegui_dashboard.team_baseline.capture", return_value=(nominal, other, self.scene)):
            with self.assertRaisesRegex(ValueError, "시작 대형"):
                capture_pair(None, None, self.row, self.arrays)
