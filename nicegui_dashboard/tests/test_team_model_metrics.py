"""Current-region metrics preserve the native formulas and immutable flight identities."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from result_service.dashboard_contract.validation import canonical_hash, sha256
from nicegui_dashboard.team_capture import write_result
from nicegui_dashboard.team_evaluation import (evaluate_reference, read_reference, reference_descriptor,
                                              store_reference, verify_reference_upgrade)
from nicegui_dashboard.team_evaluation_recovery import recover
from nicegui_dashboard.team_model import identity, model_for_flight
from nicegui_dashboard.team_model_metrics import (native_control, read_control, regional_metrics,
                                                 validate_control, metrics_path)
from nicegui_dashboard.team_baseline_record import pin_fields


def pooled(rows):
    """Sentinel result: tests require forwarding, not independent formula duplication."""
    return dict(flights=1, hours=.5, starts_per_h=6., cut_seconds_per_h=24.,
                timely_pdr=.9, relay_m=rows[0]["relay_travel_m"])


pooled.__module__ = "fast_halfsec.evaluation"


class RegionalMetricTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bundle = self.root/"bundles"/"team-1234567890"/"rev-1234567890"
        self.bundle.mkdir(parents=True)
        self.spec = dict(region="bundang", seed=1, direction="east_west", kind="continuous")
        self.row = dict(spec=self.spec, team_commit="a"*40, model_sha256="b"*64, map_sha256="c"*64,
                        controller="halfsec_planned_eight", duration_s=1800., onsets=3, cut_seconds=12.,
                        generated=100, timely=90, timely_pdr=.9, network_fraction=.95,
                        relay_travel_m=321.125, search_travel_m=99., mission_completed=True)
        self.manifest = dict(run_id=self.bundle.parent.name, bundle_revision=self.bundle.name,
                             region="bundang", controller=self.row["controller"], mode="v21_halfsec", adapter_id="team-halfsec",
                             team_commit=self.row["team_commit"], map_hash=self.row["map_sha256"],
                             model_sha256=self.row["model_sha256"], source_identity={"spec":self.spec},
                             decision_interval_s=.5, simulation_end_s=1800.,
                             artifacts={"data.sqlite":{"sha256":"d"*64}},
                             final_metrics=dict(persistent_onsets=3, persistent_cut_seconds=12.,
                                                generated_packets=100, timely_pdr=.9))
        self.control = native_control(self.row, pooled)
        self.evaluation = dict(region="bundang", metadata=dict(team_commit="a"*40, model_sha256="b"*64,
                                 map_hash="c"*64, spec=self.spec, threshold=.6),
                               summary=dict(recall=.5, precision=.75, false_per_h=.25),
                               series=dict(times=[0., .5], scores=[.2, .7], valid=[True, True]),
                               events=[], alerts=[])
        store_reference(self.bundle, self.manifest, {"_regional_evaluation":self.evaluation})
        self.seal()

    def seal(self):
        write_result(self.bundle/"manifest.json", self.manifest)
        write_result(self.bundle/"READY", {"manifest_sha256":sha256(self.bundle/"manifest.json")})

    def upgraded(self):
        data = deepcopy(self.evaluation)
        data["metadata"]["ap_evaluation"] = {"method":"official_with_ap"}
        data["summary"].update(average_precision=.8, ranking_rows=1, ranking_positives=1)
        data["series"].update(labels=[-1,2], ranking_valid=[False,True])
        return data

    def test_native_distance_is_kept_and_wrong_flight_is_rejected(self):
        self.assertEqual(self.control["summary"]["relay_m"], 321.125)
        self.assertEqual(self.control["raw"]["relay_travel_m"], 321.125)
        validate_control(self.control, self.manifest)
        for key in ("team_commit", "model_sha256", "map_hash", "controller"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_control(self.control, {**self.manifest,key:"changed"})
        changed = deepcopy(self.control)
        changed["summary"]["relay_m"] = 300.
        with self.assertRaises(ValueError):
            validate_control(changed, self.manifest)

    def test_supplement_is_pinned_and_does_not_rewrite_the_bundle(self):
        original = sha256(self.bundle/"manifest.json")
        record = dict(pin=pin_fields(self.bundle,self.manifest), control=self.control,
                      verification={"original_bundle_unchanged":True})
        write_result(metrics_path(self.bundle), {**record,"payload_sha256":canonical_hash(record)})
        self.assertEqual(read_control(self.bundle,self.manifest), self.control)
        self.assertEqual(sha256(self.bundle/"manifest.json"), original)
        record["pin"]["bundle_revision"] = "rev-other"
        write_result(metrics_path(self.bundle), {**record,"payload_sha256":canonical_hash(record)})
        with self.assertRaises(ValueError):
            read_control(self.bundle,self.manifest)

    def test_ap_upgrade_accepts_real_bundle_directory_and_preserves_prior_evaluation(self):
        before = {p.name:sha256(p) for p in self.bundle.iterdir()}
        with patch("nicegui_dashboard.team_evaluation_recovery.validate_bundle", return_value=self.manifest), \
             patch("nicegui_dashboard.team_evaluation_recovery.measure", return_value=self.upgraded()) as measure:
            result = recover(self.root,self.bundle.parent.name,self.bundle.name,Path("snapshot.json"),with_ap=True)
            self.assertEqual(result,"recovered")
            self.assertEqual(recover(self.root,self.bundle.parent.name,self.bundle.name,Path("snapshot.json"),with_ap=True),
                             "already-recorded")
            measure.assert_called_once()
        payload = read_reference(self.bundle,self.manifest)
        self.assertEqual(payload["summary"]["average_precision"],.8)
        self.assertEqual(reference_descriptor(self.bundle,self.manifest)["summary"],payload["summary"])
        self.assertEqual({p.name:sha256(p) for p in self.bundle.iterdir()},before)

    def test_upgrade_cannot_change_previous_probabilities_counts_events_or_conditions(self):
        for section,key,value in (("summary","recall",.6),("metadata","threshold",.7),
                                  ("series","scores",[.3,.7])):
            changed=self.upgraded()
            changed[section][key]=value
            with self.assertRaises(ValueError):
                verify_reference_upgrade(self.evaluation,changed)
        changed=self.upgraded()
        changed["events"]=[{"start_s":9.}]
        with self.assertRaises(ValueError):
            verify_reference_upgrade(self.evaluation,changed)

    def test_model_adds_region_without_mutating_shared_release_or_other_regions(self):
        self.manifest["regional_control"] = self.control
        self.seal()
        profile=dict(identity=identity(self.manifest),status="verified",model={"step":30},
                     controls={},forecast={})
        manifest={**self.manifest,"team_model_profile":profile}
        display=model_for_flight(manifest,self.bundle)
        self.assertNotIn("regional",profile)
        regional=display["profile"]["regional"]
        self.assertEqual(regional["control"]["relay_m"],321.125)
        self.assertEqual(regional["missing"],["forecast.average_precision"])
        self.assertFalse(regional["scope"]["official_release"])
        with patch("nicegui_dashboard.team_model_metrics.regional_metrics",return_value={"scope":{"region":"sangam"}}) as current:
            other=model_for_flight({**manifest,"region":"sangam"},self.bundle)
        current.assert_called_once()
        self.assertEqual(other["profile"]["regional"]["scope"]["region"],"sangam")
        self.assertNotIn("regional",model_for_flight({**manifest,"mode":"nominal"},self.bundle)["profile"])


class ForecastComputationTests(unittest.TestCase):
    def test_official_ap_flag_and_original_labels_are_used_with_empty_case_guard(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("Research Python's NumPy required")
        calls=[]
        labels=np.array([-1,2,-1])
        def forecast_cases(rows):
            return [dict(times=np.array([0.,.5,1.]),scores={"joint":np.array([.2,.1,np.nan])},label=labels)]
        def evaluate(rows,variant,threshold,with_ap=False):
            calls.append(with_ap)
            result=dict(events=0,alerts=0,hits=0,median_lead_s=None)
            if with_ap:
                result.update(average_precision=.3,ranking_rows=1,ranking_positives=1)
            return result
        campaign=SimpleNamespace(forecast_cases=forecast_cases,evaluate=evaluate)
        arrays=dict(network=np.ones(3,bool),plan_frames=np.array([0,1]),scores=np.array([.1,.2]),
                    comparison_scores=np.array([.2,.1]))
        row=dict(spec={"region":"bundang"},cuts=[],duration_s=1.5)
        actual=evaluate_reference(campaign,row,arrays,.6,{})
        self.assertEqual(calls,[True])
        self.assertEqual(actual["series"]["ranking_valid"],[False,True,False])
        labels[:]=-1
        empty=evaluate_reference(campaign,row,arrays,.6,{})
        self.assertEqual(calls,[True,False])
        self.assertIsNone(empty["summary"]["average_precision"])
        self.assertEqual(empty["metadata"]["ap_evaluation"]["empty_reason"],"no_eligible_observations")


class PublishedRegionalControlTests(unittest.TestCase):
    def test_baseline_control_has_its_own_identity_and_changes_shared_revision(self):
        from nicegui_dashboard.tests import test_team_bundle as fixtures
        if fixtures.np is None:
            self.skipTest("Research Python's NumPy required")
        from nicegui_dashboard.team_bundle import publish_capture
        from nicegui_dashboard.team_baseline_record import nominal_bundle
        fixture=fixtures.TeamBundleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        row=dict(fixture.record,deployment="s2r1",full_mission_budget_s=5000.,shared_cells=3,target_cells=5,
                 map_input_source="team_registry",source_study_role="test",relay_travel_m=321.125,search_travel_m=99.)
        nominal=dict(row,controller="nominal_no_relocation",relay_travel_m=123.5)
        original=publish_capture(row,fixture.a,fixture.scene,fixture.root/"store",nominal=(nominal,fixture.a))
        control=native_control(nominal,pooled)
        control["summary"].update(hours=row["duration_s"]/3600.,timely_pdr=row["timely_pdr"])
        nominal["_regional_control"]=control
        updated=publish_capture(row,fixture.a,fixture.scene,fixture.root/"store",nominal=(nominal,fixture.a))
        self.assertNotEqual(original["revision"],updated["revision"])
        baseline=nominal_bundle(fixture.root/"store"/"bundles"/updated["run_id"]/updated["revision"])
        manifest=json.loads((baseline/"manifest.json").read_bytes())
        self.assertEqual(manifest["regional_control"]["identity"]["controller"],"nominal_no_relocation")
        self.assertEqual(manifest["regional_control"]["summary"]["relay_m"],123.5)

    def test_new_flight_preserves_native_distance_and_adds_it_to_revision_identity(self):
        from nicegui_dashboard.tests import test_team_bundle as fixtures
        if fixtures.np is None:
            self.skipTest("Research Python's NumPy required")
        from nicegui_dashboard.team_bundle import publish_capture
        fixture=fixtures.TeamBundleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        row=fixture.record
        row.update(relay_travel_m=321.125,search_travel_m=99.)
        original=publish_capture(row,fixture.a,fixture.scene,fixture.root/"store")
        control=native_control(row,pooled)
        control["summary"]["hours"]=row["duration_s"]/3600.
        control["summary"]["timely_pdr"]=row["timely_pdr"]
        row["_regional_control"]=control
        added=publish_capture(row,fixture.a,fixture.scene,fixture.root/"store")
        self.assertEqual(original["run_id"],added["run_id"])
        self.assertNotEqual(original["revision"],added["revision"])
        bundle=fixture.root/"store"/"bundles"/added["run_id"]/added["revision"]
        manifest=json.loads((bundle/"manifest.json").read_bytes())
        self.assertEqual(manifest["regional_control"]["raw"]["relay_travel_m"],321.125)
        self.assertEqual(manifest["regional_control"]["summary"]["relay_m"],321.125)
        self.assertEqual(manifest["final_metrics"]["pdr"],.9)


if __name__ == "__main__":
    unittest.main()
