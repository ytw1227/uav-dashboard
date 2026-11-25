"""Regression checks for direction, clock, cause and identity in native replay data."""
import sqlite3
import unittest

try:
    import numpy as np
except ImportError:
    np = None

from nicegui_dashboard.team_bundle import packed, SCHEMA
from nicegui_dashboard.team_incidents import timeline_events
from nicegui_dashboard.team_telemetry import margin_witnesses, radio_rows


class ReceiptTests(unittest.TestCase):
    def test_publication_join_and_open_interval(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript(SCHEMA)
        for start in (0., .5, 1.):
            frame = dict(interval_start_s=start,metrics={"search_connected":1})
            db.execute("INSERT INTO rf_frames VALUES(?,?,?,?)", (start+.5,start,start+.5,packed(frame)))
            pos = dict(uavs=[dict(id="r",x=start,y=1.)])
            db.execute("INSERT INTO positions VALUES(?,?)",(start,packed(pos)))
        db.execute("INSERT INTO positions VALUES(?,?)",(1.5,packed(dict(uavs=[dict(id="r",x=1.5,y=1.)]))))
        request = dict(id="a",type="intervention",start_s=.5,related_uav_ids=["r"],source_detail=dict(
            new_offset=[0,20],published_s=.7,reason="recovery"))
        received = dict(id="b",type="applied_command",start_s=.8,related_uav_ids=["r"],
                        source_command_s=.7,offset_xy_m=[0,20])
        rows = timeline_events(db,[request,received])
        self.assertEqual(request["delivered_at_s"],.8)
        self.assertEqual(request["outcome_available_s"],1.5)
        self.assertNotIn("motion_guard_blocked",request)
        interval = next(e for e in rows if e["type"]=="relocation")
        self.assertEqual((interval["start_s"],interval["end_s"]),(.8,1.5))
        self.assertTrue(interval["right_censored"])


@unittest.skipUnless(np is not None, "Requires the research environment")
class RadioTests(unittest.TestCase):
    def test_ack_uses_reverse_channel_and_expired_interval_clears_ratio(self):
        arrays = {k:np.zeros((2,2,2)) for k in ("ack_stamp","ack_attempts","ack_successes","radio_loss","radio_shadow","radio_sinr")}
        arrays["beacon_rx"] = np.array([[0.,1,0,-81.,15.]])
        arrays["ack_attempts"][0,0,1] = 4
        arrays["ack_successes"][0,0,1] = 1
        arrays["ack_stamp"][0,0,1] = .305
        arrays["radio_sinr"][0,0,1],arrays["radio_sinr"][0,1,0] = 9.,37.
        rows = {t:row for t,a,b,row in radio_rows(arrays,["s","r"]) if (a,b)==("s","r")}
        self.assertNotIn("ack_success_ratio_in_interval",rows[.305]["values"])
        self.assertEqual(rows[.305]["values"]["last_ack_sinr_db"],37.)
        self.assertEqual(rows[.5]["values"]["ack_success_ratio_in_interval"],.25)
        self.assertAlmostEqual(rows[1.]["ages"]["last_ack_sinr_db"],.695)
        self.assertFalse(rows[1.]["valid"]["ack_success_ratio_in_interval"])

    def test_widest_route_and_tie_break(self):
        try:
            import scipy.special
        except ImportError:
            self.skipTest("Requires the research environment's SciPy")
        sinr = np.array([[[0.,0.,16.],[0.,0.,19.],[16.,19.,0.]]])
        links = sinr>10
        verified = margin_witnesses(sinr,links,2)
        self.assertEqual(verified["margin_pair"].tolist(),[[0,1]])
        self.assertEqual(verified["margin_path"].tolist(),[[0,2,1]])
        self.assertEqual(verified["margin_edge"].tolist(),[[0,2]])
        with self.assertRaises(ValueError):
            margin_witnesses(sinr,np.zeros_like(links),2)
