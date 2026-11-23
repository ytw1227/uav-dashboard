"""Contract tests for team inference replay. Run with the research Python (NumPy)."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

try:
    import numpy as np
except ImportError:
    np = None

from nicegui_dashboard.team_bundle import publish_capture, validate_capture


@unittest.skipUnless(np is not None, "Team inference adapter needs the research environment's NumPy")
class TeamBundleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.scene = self.root / "map.json"
        self.scene.write_text(json.dumps({"metadata": {"local_bounds": [-10, -10, 10, 10]}, "buildings": []}))
        self.record = dict(spec=dict(region="fixture", seed=1, kind="clear", direction="east_west"),
            cuts=[[.5,1.5]], onsets=1, cut_seconds=1., duration_s=2., network_fraction=.5,
            total_drones=3, search_drones=2, generated=10, delivered=9, timely=7, timely_pdr=.7,
            mission_completed=True, team_commit="a"*40, map_sha256=hashlib.sha256(self.scene.read_bytes()).hexdigest(),
            model_sha256="b"*64, controller="test", split="test", altitude_m=20., decision_interval_s=.5,
            team_capture_version="1", max_seconds_requested=None, safety_passed=True, packet_conserved=True,
            started_at="2026-11-23T00:00:00Z", completed_at="2026-11-23T00:01:00Z", events=[], applied_changes=[])
        links = np.ones((4,3,3),dtype=bool)
        links[1:3,1,:] = False
        links[1:3,:,1] = False
        self.a = dict(positions=np.zeros((4,3,2)), final_positions=np.ones((3,2)),
            links=links, network=np.array([True,False,False,True]), margin_db=np.array([1,-1,-1,1]),
            applied_offsets=np.zeros((4,1),dtype=int), offset_xy_m=np.zeros((1,2)),
            packet_counters=np.array([[2,2,2],[4,4,3],[7,6,5],[10,8,7]]),
            plan_frames=np.array([1,2]), scores=np.array([.1,.8]), support_counts=np.array([0,2]),
            beacon_rx=np.array([[.5,0,2,-67,31]]))

    def test_published_record_and_native_clocks(self):
        self.record.update(map_input_source="dashboard_demonstration", source_study_role="legacy_protected",
                           split="external_demonstration", model_threshold=.4)
        result = publish_capture(self.record,self.a,self.scene,self.root/"store")
        from dashboard_contract.validation import validate_bundle
        from dashboard_service.query import events, timeline
        bundle = self.root/"store"/"bundles"/result["run_id"]/result["revision"]
        manifest = validate_bundle(bundle)
        self.assertEqual(manifest["team_commit"],self.record["team_commit"])
        self.assertEqual(manifest["model_threshold"],.4)
        self.assertEqual(manifest["map_input_source"],"dashboard_demonstration")
        self.assertEqual(manifest["source_study_role"],"legacy_protected")
        self.assertEqual(manifest["split"],"external_demonstration")
        self.assertEqual(manifest["final_metrics"]["pdr"],.9)
        self.assertEqual(manifest["final_metrics"]["timely_pdr"],.7)
        loaded = timeline(manifest,bundle,0,2,1)
        self.assertEqual(loaded["rf_frames"][-1]["metrics"]["pdr_so_far"],.8)
        self.assertEqual(loaded["positions"][-1]["uavs"][0]["x"],1.)
        self.assertIsNone(loaded["decisions"][1]["fleet_score"])
        self.assertEqual(loaded["decisions"][2]["fleet_score"],.8)
        self.assertEqual(loaded["observations"][0]["observer_id"],"uav-2")
        cuts = events(manifest,bundle,event_type="persistent_search_outage")["events"]
        self.assertEqual([(e["start_s"],e["end_s"]) for e in cuts],[(.5,1.5)])
        from nicegui_dashboard.team_incidents import read_incident
        book = read_incident(bundle, manifest, "book", cuts)
        self.assertEqual(book["revision"], result["revision"])
        case, = book["events"]
        self.assertEqual(case["id"], cuts[0]["id"])
        self.assertEqual(case["window"], [0., 2.])
        self.assertEqual(case["recovery_s"], 1.5)
        self.assertEqual(case["search_groups"], [["uav-0"], ["uav-1"]])
        self.assertEqual(len(case["crossing_lost_links"]), 2)
        self.assertEqual(case["interventions"], [])

    def test_partial_diagnostic_and_inconsistent_counts_never_publish(self):
        for changes in (dict(max_seconds_requested=2.), dict(onsets=2), dict(network_fraction=.75),
                        dict(generated=11), dict(timely=6, timely_pdr=.6), dict(timely_pdr=.8)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_capture({**self.record,**changes},self.a)
