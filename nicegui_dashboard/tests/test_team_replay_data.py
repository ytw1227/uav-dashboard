import gzip
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from result_service.dashboard_contract.validation import sha256
from nicegui_dashboard.managed_service import SERVICE_PROJECT
from nicegui_dashboard.team_bundle import packed, SCHEMA
from nicegui_dashboard.team_forecast_record import FLEET_FORECAST_HEAD, forecast_digest, forecast_path
from nicegui_dashboard.team_replay_data import display_manifest, margin_series, observations, overview_bytes, register_team_replay_routes


class TeamReplayDataTests(unittest.TestCase):
    def test_new_metrics_and_cut_intervals_use_the_current_flight(self):
        stored = {"mode": "v21_halfsec", "final_metrics": {"ctr": .75, "pdr": .9},
                  "metric_definitions": {"ctr": {"unit": "ratio"}}, "observation_definitions": {}}
        events = [{"type": "search_outage", "start_s": 3., "end_s": 3.5},
                  {"type": "persistent_search_outage", "start_s": 0., "end_s": 1.5}]
        adapted = display_manifest(stored, events)
        self.assertEqual(adapted["recorded_mode"], "v21_halfsec")
        self.assertEqual(adapted["final_metrics"]["search_connected_mean"], .75)
        self.assertEqual(adapted["raw_metadata"]["persistent_cut_intervals"], [[0., 1.5]])
        self.assertEqual(adapted["final_metrics"]["persistent_max_s"], 1.5)
        self.assertNotIn("search_connected_mean", stored["final_metrics"])

    def test_overview_and_radio_read_only_recorded_samples_from_the_requested_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = sqlite3.connect(root / "data.sqlite")
            with db:
                db.executescript(SCHEMA)
                for time, src, dst, value in ((1., "s", "r", 12.), (2., "s", "r", 0.), (1., "r", "s", 40.)):
                    data = dict(observed_at_s=time, available_at_s=time, observer_id=src, peer_id=dst,
                                values={"beacon_sinr_db": value}, valid={"beacon_sinr_db": True},
                                ages={"beacon_sinr_db": 0.})
                    db.execute("INSERT INTO observations VALUES (?,?,?,?)", (time, src, dst, packed(data)))
                frame = {"interval_start_s": 0., "interval_end_s": .5, "metrics": {"network_margin_db": -1.25}}
                db.execute("INSERT INTO rf_frames VALUES (?,?,?,?)", (.5, 0., .5, packed(frame)))
            db.close()
            m = {"run_id": "team-fixture", "fleet": [{"id": "s"}, {"id": "r"}]}
            pair = observations(root, m, "s", "r", 2., 2., 1.)
            self.assertEqual([x["values"] for x in pair["observations"]], [{"beacon_received_sinr_db": 0.}])
            self.assertEqual(pair["at"]["observations"][0]["values"]["beacon_received_sinr_db"], 12.)
            self.assertTrue(pair["observations"][0]["valid"]["beacon_received_sinr_db"])
            self.assertFalse(observations(root, m, "s", "r", 0., .5, .5)["at"]["observations"])
            overview = json.loads(gzip.decompress(overview_bytes(root, ())))
            self.assertEqual(overview["rf_frames"], [frame])
            self.assertEqual(margin_series(root, ())["values"], [-1.25])


class SupplementalForecastTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SERVICE_PROJECT))
        self.addCleanup(sys.path.remove, str(SERVICE_PROJECT))
        from dashboard_tests.fixtures import make_bundle, seal
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Path(tmp.name)
        self.root, manifest = make_bundle(store)
        manifest.update(adapter_id="team-halfsec", region=manifest["map_id"], model_sha256="b"*64,
                        team_commit="a"*40, mode="v21_halfsec", head_definitions={})
        manifest["final_metrics"]["ctr"] = .5
        manifest["metric_definitions"]["ctr"] = dict(label="CTR")
        manifest["clocks"]["decision_dt_s"] = .5
        manifest["validation_report"]["counts"]["decisions"] = 12
        db = sqlite3.connect(self.root / "data.sqlite")
        with db:
            db.execute("DELETE FROM decisions")
            for i in range(12):
                d = dict(decision_time_s=i*.5, support_count=int(i > 0), fleet_score=.25 if i else None,
                         actions=[], predictions=[])
                db.execute("INSERT INTO decisions VALUES(?,?)", (i*.5, packed(d)))
        db.close()
        seal(self.root, manifest)
        self.m = manifest
        self.side = forecast_path(store, manifest["run_id"], manifest["bundle_revision"])
        self.side.parent.mkdir(parents=True)
        self.sample = dict(schema_version=1, run_id=manifest["run_id"], bundle_revision=manifest["bundle_revision"],
                           manifest_sha256=sha256(self.root/"manifest.json"),
                           data_sha256=manifest["artifacts"]["data.sqlite"]["sha256"], team_commit=manifest["team_commit"],
                           model_sha256=manifest["model_sha256"], map_hash=manifest["map_hash"],
                           head_definition=FLEET_FORECAST_HEAD, model_threshold=.4, probabilities=[None]+[.815]*11,
                           pre_action_connected=[None]+[True]*4+[False]*7)
        api = FastAPI()
        register_team_replay_routes(api, store)
        self.http = TestClient(api)
        self.addCleanup(self.http.close)
        self.base = "/part2/team/"+manifest["run_id"]+"/"+manifest["bundle_revision"]+"/"

    def publish(self, override=None):
        data = dict(self.sample, **(override or {}))
        data["payload_sha256"] = forecast_digest(data)
        tmp = self.side.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.side)

    def get(self, name):
        response = self.http.get(self.base+name)
        self.assertEqual(response.status_code, 200, response.text[:100])
        return response.json()

    def test_new_supplement_updates_warm_routes_and_keeps_original_clocks_and_data(self):
        before = self.get("overview")
        self.assertNotIn("fleet_onset_probability", self.get("descriptor")["manifest"]["head_definitions"])
        self.publish()
        shown = self.get("descriptor")["manifest"]
        self.assertEqual(shown["head_definitions"]["fleet_onset_probability"], FLEET_FORECAST_HEAD)
        self.assertEqual(self.get("manifest"), shown)
        current = self.get("overview")
        self.assertEqual(current["rf_frames"], before["rf_frames"])
        self.assertEqual(current["positions"], before["positions"])
        for i, (new, old) in enumerate(zip(current["decisions"], before["decisions"], strict=True)):
            self.assertEqual(new.pop("fleet_onset_probability"), self.sample["probabilities"][i])
            self.assertIs(new.pop("pre_action_connected"), self.sample["pre_action_connected"][i])
            self.assertEqual(new, old)
        self.assertEqual(sha256(self.root/"manifest.json"), self.sample["manifest_sha256"])
        self.assertEqual(sha256(self.root/"data.sqlite"), self.sample["data_sha256"])
        self.side.unlink()
        self.assertEqual(self.get("overview"), before)

    def test_mismatched_pin_checksum_or_mask_cannot_supply_probability(self):
        for change in (dict(run_id="wrong-flight"), dict(data_sha256="0"*64),
                       dict(pre_action_connected=[None]+[True]*11), dict(probabilities=[.8]*12)):
            self.publish(change)
            with self.assertLogs("nicegui_dashboard.team_replay_data", level="WARNING"):
                self.assertNotIn("fleet_onset_probability", self.get("manifest")["head_definitions"])
            self.assertNotIn("fleet_onset_probability", self.get("overview")["decisions"][1])
        self.publish()
        self.side.write_text(self.side.read_text().replace("0.815", "0.816"), encoding="utf-8")
        with self.assertLogs("nicegui_dashboard.team_replay_data", level="WARNING"):
            self.assertNotIn("fleet_onset_probability", self.get("manifest")["head_definitions"])

    def test_native_predictions_win_even_if_supplement_present(self):
        from dashboard_tests.fixtures import seal
        self.m["head_definitions"]["fleet_onset_probability"] = dict(FLEET_FORECAST_HEAD)
        with sqlite3.connect(self.root/"data.sqlite") as db:
            db.execute("UPDATE decisions SET payload=? WHERE time_s=.5", (packed(dict(
                decision_time_s=.5, support_count=1, fleet_score=.25, fleet_onset_probability=.348,
                pre_action_connected=True)),))
        db.close()
        seal(self.root, self.m)
        self.publish()
        self.assertEqual(self.get("overview")["decisions"][1]["fleet_onset_probability"], .348)
        self.assertNotIn("model_threshold", self.get("manifest"))
