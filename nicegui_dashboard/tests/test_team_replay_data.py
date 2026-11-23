import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from nicegui_dashboard.team_bundle import packed, SCHEMA
from nicegui_dashboard.team_replay_data import display_manifest, margin_series, observations, overview_bytes


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
