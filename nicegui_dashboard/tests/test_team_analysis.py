from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from nicegui_dashboard.team_analysis import read_analysis
from nicegui_dashboard.team_bundle import SCHEMA, packed


class RecordedAnalysisTests(unittest.TestCase):
    def test_historic_sangam_shape_and_native_factors_follow_selected_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/"team-first"/"rev-one"
            root.mkdir(parents=True)
            manifest = dict(run_id=root.parent.name, bundle_revision=root.name,
                            fleet=[dict(id="uav-0",role="search"), dict(id="uav-1",role="search")])
            database = root/"data.sqlite"
            with closing(sqlite3.connect(database)) as db, db:
                db.executescript(SCHEMA)
                for i in range(13):
                    t = .5*i
                    frame = dict(metrics=dict(network_margin_db=12 if i<12 else -3),
                                 components=[["uav-0"],["uav-1"]])
                    db.execute("INSERT INTO rf_frames VALUES(?,?,?,?)",(t+.5,t,t+.5,packed(frame)))
                db.execute("INSERT INTO events VALUES(?,?,?,?,?)",("cut-6",6,7,"persistent_search_outage","{}"))
            historical = read_analysis(root,manifest)["cut-6"]
            self.assertEqual((historical["pattern"],historical["jump_db"],historical["factors"]),("abrupt",15,[]))
            self.assertTrue(historical["evidence_note"])
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("CREATE TABLE incident_analysis(time_s REAL PRIMARY KEY,payload BLOB)")
                db.execute("INSERT INTO incident_analysis VALUES(?,?)",(6,packed(dict(factors=[["distance_change"]]))))
            self.assertEqual(read_analysis(root,manifest)["cut-6"]["factors"],[["distance_change"]])
            with self.assertRaises(ValueError):
                read_analysis(root, dict(manifest,run_id="team-second"))
