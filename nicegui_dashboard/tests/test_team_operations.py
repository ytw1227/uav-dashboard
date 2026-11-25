"""Pins, causal operation records and pass-through observation; no research changes."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nicegui_dashboard import team_entry
from nicegui_dashboard.team_operations import (identity_for, load_operations, operations_digest,
                                               operations_path, validate_operations)
from nicegui_dashboard.team_operations_capture import observed_capture, progress_sample
from nicegui_dashboard.team_operations_probe import OperationProbe


class OperationContractTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.manifest = dict(run_id="team-12345678", bundle_revision="rev-12345678", region="bundang",
                             controller="halfsec_planned_eight", mode="v21_halfsec", team_commit="a"*40,
                             map_hash="b"*64, model_sha256="c"*64, simulation_end_s=1.,
                             artifacts={"data.sqlite": {"sha256": "d"*64}},
                             fleet=[dict(id=f"uav-{i}", role="search" if i<3 else "relay") for i in range(8)])
        bundle = self.root / "bundles" / self.manifest["run_id"] / self.manifest["bundle_revision"]
        bundle.mkdir(parents=True)
        (bundle / "manifest.json").write_text(json.dumps(self.manifest))
        self.identity = identity_for(self.root, self.manifest)
        roles = [dict(drone_id=item["id"], role=item["role"], duty="search" if i<3 else "mobile_relay")
                 for i, item in enumerate(self.manifest["fleet"])]
        self.payload = dict(schema_version=1, status="available", identity=self.identity, roles=roles,
                            plan={"station": None, "windows": []}, windows=[], moves=[], checks=[],
                            progress=[dict(time_s=i*.5, state="search", stage=i, waypoints_reached=i,
                                           waypoints_total=2, coverage=i/2, heights_m=[50.]*8,
                                           clearing=False, entry_waiting=False, descent_check=False)
                                      for i in range(3)],
                            summary=dict(waypoints_total=2, waypoints_reached=2, coverage=1., duration_s=1.),
                            verification={"original_bundle_unchanged": True})
        self.seal()

    def seal(self):
        self.payload["payload_sha256"] = operations_digest(self.payload)

    def publish(self):
        path = operations_path(self.root, self.manifest["run_id"], self.manifest["bundle_revision"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.payload))
        return path

    def test_missing_and_other_region_are_explicit_not_fabricated_records(self):
        self.assertEqual(load_operations(self.root, self.manifest)["status"], "not_recorded")
        other = dict(self.manifest, region="sangam")
        self.assertEqual(load_operations(self.root, other)["status"], "not_recorded")
        nominal = dict(self.manifest, mode="nominal")
        self.assertEqual(load_operations(self.root, nominal)["status"], "unsupported")

    def test_display_manifest_aliases_preserve_exact_source_identity(self):
        self.publish()
        display = dict(self.manifest, mode="v21", recorded_mode="v21_halfsec", final_metrics={"alias": .5})
        self.assertEqual(load_operations(self.root, display), self.payload)

    def test_changed_run_revision_commit_map_model_or_bundle_cannot_attach(self):
        for key in self.identity:
            with self.subTest(key=key):
                changed = deepcopy(self.payload)
                changed["identity"][key] = "different"
                changed["payload_sha256"] = operations_digest(changed)
                with self.assertRaises(ValueError):
                    validate_operations(changed, self.manifest, self.identity)
        self.publish()
        manifest_path = self.root / "bundles" / self.manifest["run_id"] / self.manifest["bundle_revision"] / "manifest.json"
        manifest_path.write_text(manifest_path.read_text()+" ")
        with self.assertRaises(ValueError):
            load_operations(self.root, self.manifest)

    def test_no_silent_gaps_reversed_progress_wrong_drone_or_unverified_sidecar(self):
        original = deepcopy(self.payload)
        mutations = [lambda p: p["progress"].pop(),
                     lambda p: p["progress"][1].update(time_s=.7),
                     lambda p: p["progress"][2].update(stage=0, waypoints_reached=0),
                     lambda p: p["roles"][0].update(drone_id="uav-8"),
                     lambda p: p["verification"].update(original_bundle_unchanged=False),
                     lambda p: p["checks"].append(dict(time_s=2., kind="descent_safe", passed=True, drone_id="uav-4"))]
        for mutate in mutations:
            changed = deepcopy(original)
            mutate(changed)
            changed["payload_sha256"] = operations_digest(changed)
            with self.assertRaises(ValueError):
                validate_operations(changed, self.manifest, self.identity)

    def test_path_traversal_and_unsealed_edit_are_rejected(self):
        with self.assertRaises(ValueError):
            operations_path(self.root, "../bundle", "rev-12345678")
        self.payload["progress"][1]["heights_m"][4] = 80.
        self.publish()
        with self.assertRaises(ValueError):
            load_operations(self.root, self.manifest)

    def test_new_capture_embeds_sealed_operations_without_a_manifest_hash_cycle(self):
        import numpy as np
        from nicegui_dashboard.team_operations import store_operations, recording_fingerprint
        row = dict(placement=self.payload["plan"], altitude_windows=[], altitude_moves=[],
                    coverage=1., waypoints_reached=2, waypoints_total=2, duration_s=1.,
                    cleared_stages=0, clearance_edges=0, mission_completed=True,
                    _operations_recording=dict(schema_version=1,source="native_navigation_callbacks",
                                                progress=deepcopy(self.payload["progress"]), checks=[]))
        original = deepcopy(row)
        arrays = dict(network=np.array([True,True]),display_margin=np.array([20.,19.]),
                      altitudes=np.full((2,8),50.,dtype=np.float32))
        bundle = self.root/"bundles"/self.manifest["run_id"]/self.manifest["bundle_revision"]
        store_operations(bundle,self.manifest,row,arrays)
        (bundle/"manifest.json").write_text(json.dumps(self.manifest))
        loaded=load_operations(self.root,self.manifest)
        self.assertEqual(loaded["status"],"available")
        self.assertEqual(loaded["progress"][0]["network_margin_db"],20.)
        self.assertNotIn("manifest_sha256",loaded["identity"])
        self.assertEqual(row,original)
        changed=deepcopy(row)
        changed["_operations_recording"]["checks"].append({"changed":True})
        self.assertNotEqual(recording_fingerprint(row),recording_fingerprint(changed))
        (bundle/"operations.json").write_text((bundle/"operations.json").read_text(encoding="utf-8")+" ",encoding="utf-8")
        with self.assertRaises(ValueError):
            load_operations(self.root,self.manifest)


class PassiveObserverTests(unittest.TestCase):
    def test_budget_terminal_snapshot_reads_state_without_an_extra_native_call(self):
        returned=object()
        nav=SimpleNamespace(update=Mock(return_value=returned),confirm=Mock(),descent_safe=Mock())
        original=nav.update
        original_confirm,original_safe=nav.confirm,nav.descent_safe
        probe=OperationProbe()
        with patch("nicegui_dashboard.team_operations_probe.progress_sample",
                   side_effect=lambda nav,now:{"time_s":now,"heights_m":[60.]*8}) as sample:
            probe.attach(nav)
            for now in (7199.5,7199.6,7199.7,7199.8,7199.9):
                self.assertIs(nav.update(now),returned)
            output=probe.recording(end_time_s=7200.)
            self.assertEqual(original.call_count,5)
            self.assertEqual(sample.call_count,2)
            self.assertEqual(output["progress"][-1]["time_s"],7200.)
            self.assertEqual(output["progress"][-1]["observed_after_update_s"],7199.9)
            self.assertTrue(output["progress"][-1]["terminal_snapshot"])
            probe.recording(end_time_s=7200.)
            self.assertEqual(sample.call_count,2)
            self.assertEqual(len(output["progress"]),2)
        original_confirm.assert_not_called()
        original_safe.assert_not_called()

    def test_normal_completion_does_not_duplicate_or_relabel_its_native_sample(self):
        nav=SimpleNamespace(update=Mock(),confirm=Mock(),descent_safe=Mock())
        probe=OperationProbe()
        with patch("nicegui_dashboard.team_operations_probe.progress_sample",
                   side_effect=lambda nav,now:{"time_s":now}) as sample:
            probe.attach(nav)
            nav.update(1729.)
            nav.update(1729.5)
            output=probe.recording(end_time_s=1729.5)
            self.assertEqual(sample.call_count,2)
            self.assertEqual(output["progress"][-1],{"time_s":1729.5})
            with self.assertRaises(ValueError):
                probe.recording(end_time_s=1730.5)

    def test_clearance_and_path_completion_have_separate_native_meanings(self):
        class VisibleCells:
            def __getitem__(self, mask):
                return SimpleNamespace(sum=lambda: 9)
        nav = SimpleNamespace(survey=SimpleNamespace(waypoints=list(range(598)), observable=10),
                              covered=VisibleCells(), city=SimpleNamespace(observable=None),
                              record=None, move=None, pending=None, entry_waiting=False, prove=None,
                              clearing=True, finished_at=None, windows_log=[], window=None,
                              stage=497, stalled=202, relay=4, heights=[50.]*8)
        clearing = progress_sample(nav, 1370.)
        self.assertEqual(clearing["state"], "clearing")
        self.assertEqual(clearing["waypoints_reached"], 497)
        self.assertEqual(clearing["stalled_s"], 20.2)
        nav.stage, nav.finished_at = 598, 1699.5
        self.assertEqual(progress_sample(nav, 1699.5)["state"], "complete")

    def test_native_update_and_checks_called_once_and_return_objects_unchanged(self):
        returned = object()
        confirmation = object()
        descent = object()
        nav = SimpleNamespace(stage=7)
        nav.confirm = Mock(return_value=confirmation)
        nav.descent_safe = Mock(return_value=descent)
        original_confirm, original_safe = nav.confirm, nav.descent_safe
        def update(now):
            self.assertIs(nav.confirm(now, .2, 4), confirmation)
            self.assertIs(nav.descent_safe(now, 4, 55.), descent)
            return returned
        original_update = nav.update = Mock(side_effect=update)
        probe = OperationProbe()
        with patch("nicegui_dashboard.team_operations_probe.progress_sample", side_effect=lambda nav,now:{"time_s":now}):
            self.assertIs(probe.attach(nav), nav)
            for now in (0., .1, .2, .3, .4, .5):
                self.assertIs(nav.update(now), returned)
        self.assertEqual(original_update.call_count, 6)
        self.assertEqual(original_confirm.call_count, 6)
        self.assertEqual(original_safe.call_count, 6)
        self.assertEqual(probe.progress, [{"time_s":0.}, {"time_s":.5}])
        self.assertEqual(len(probe.checks), 12)
        self.assertEqual(probe.checks[0]["kind"], "confirmation")
        self.assertEqual(probe.checks[1]["kind"], "descent_safe")
        self.assertEqual(probe.recording()["source_update_s"], .1)
        with self.assertRaises(ValueError):
            probe.attach(nav)

    def test_observed_replay_reuses_the_normal_single_capture(self):
        recording=dict(source="native_navigation_callbacks",progress=[{"time_s":0.}],checks=[])
        row={"_operations_recording":recording}
        with patch("nicegui_dashboard.team_operations_capture.capture",return_value=(row,{},Path("scene"))) as run:
            returned=observed_capture(Path("code"),Path("core"),{"region":"sangam"},"a"*40)
        run.assert_called_once_with(Path("code"),Path("core"),{"region":"sangam"},"a"*40,include_evaluation=False)
        self.assertIs(returned[0],row)
        self.assertIs(returned[3],recording["progress"])



if __name__ == "__main__":
    unittest.main()
