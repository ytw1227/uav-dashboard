import copy
from pathlib import Path
import sys
import tempfile
import unittest

from nicegui_dashboard.team_evaluation import ARTIFACT, event_rows, read_reference, store_reference


class ReferenceEvaluationTests(unittest.TestCase):
    def test_alert_onsets_and_half_open_matching_preserve_the_official_counts(self):
        cuts = [(0., 1.), (5., 6.5), (7., 8.), (19., 20.)]
        # Rising edges at 0, 2, 12 and 17; 12+7 is excluded, 17+3 is too late.
        times = [0., .5, 1., 2., 2.5, 12., 13., 17.]
        scores = [.5, .8, None, .7, None, .5, .2, .6]
        summary = dict(events=3, alerts=4, hits=2, median_lead_s=5.)
        events, alerts = event_rows(cuts, times, scores, .5, 20., summary)
        self.assertEqual([a["time_s"] for a in alerts], [0., 2., 12., 17.])
        self.assertEqual([e["warning_time_s"] for e in events], [0., 2., None])
        self.assertIsNone(events[-1]["end_s"])
        with self.assertRaises(ValueError):
            event_rows(cuts, times, scores, .5, 20., dict(summary, hits=3))

    def test_empty_results_are_supported(self):
        summary = dict(events=0, alerts=0, hits=0, median_lead_s=None)
        self.assertEqual(event_rows([], [0.], [None], .7, .5, summary), ([], []))

    def test_storage_pins_region_model_spec_version_revision_and_file(self):
        spec = dict(region="sangam", seed=3, kind="clear", direction="north_south")
        meta = dict(team_commit="one", map_hash="map", model_sha256="model")
        manifest = dict(**meta, region="sangam", source_identity=dict(spec=spec), artifacts={},
                        run_id="team-a", bundle_revision="rev-b")
        data = dict(region="sangam", metadata=dict(**meta,spec=spec), summary={"hits":2})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(read_reference(root, manifest))
            store_reference(root, manifest, {"_regional_evaluation":data})
            self.assertEqual(read_reference(root, manifest), data)
            for key in ("bundle_revision", "team_commit", "map_hash", "model_sha256"):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    read_reference(root, dict(manifest, **{key:"changed"}))
            changed = copy.deepcopy(manifest)
            changed["source_identity"]["spec"]["seed"] += 1
            with self.assertRaises(ValueError):
                read_reference(root, changed)
            (root/ARTIFACT).write_bytes(b"{}")
            with self.assertRaises(ValueError):
                read_reference(root, manifest)

    def test_published_evaluation_route_is_bound_to_the_displayed_revision(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from nicegui_dashboard.ingest import load_store, publish
        from nicegui_dashboard.managed_service import SERVICE_PROJECT
        from nicegui_dashboard.team_replay_data import register_team_replay_routes
        sys.path.insert(0, str(SERVICE_PROJECT))
        self.addCleanup(sys.path.remove, str(SERVICE_PROJECT))
        from dashboard_tests.fixtures import make_bundle, seal

        with tempfile.TemporaryDirectory() as tmp:
            source, output = (Path(tmp) / part for part in ("source", "output"))
            bundle, manifest = make_bundle(source)
            region = manifest["map_id"]
            spec = {key:manifest[key] for key in ("direction", "seed", "kind")}
            spec["region"] = region
            meta = dict(map_hash=manifest["map_hash"], model_sha256="a"*64, team_commit="b"*40)
            manifest.update(adapter_id="team-halfsec", mode="v21_halfsec", region=region, **meta)
            manifest["source_identity"]["spec"] = spec
            manifest["final_metrics"]["ctr"] = .5
            manifest["metric_definitions"]["ctr"] = {"label":"CTR"}
            payload = dict(region=region, metadata=dict(**meta,spec=spec), summary={"events":1,"hits":1})
            store_reference(bundle, manifest, {"_regional_evaluation":payload})
            seal(bundle, manifest)
            store = load_store(SERVICE_PROJECT, output, quota_gib=1)
            try:
                publish(bundle, store)
            finally:
                store.close()
            api = FastAPI()
            register_team_replay_routes(api, output)
            pin = f"{manifest['run_id']}/{manifest['bundle_revision']}"
            with TestClient(api) as client:
                self.assertEqual(client.get(f"/part2/team/{pin}/evaluation").json(), payload)
                descriptor = client.get(f"/part2/team/{pin}/descriptor").json()["evaluation"]
                self.assertEqual(descriptor["chunk"], f"team-evaluation/{pin}")
                self.assertEqual(descriptor["summary"], payload["summary"])
                self.assertEqual(client.get(f"/part2/team/{manifest['run_id']}/unknownrev/evaluation").status_code, 404)
