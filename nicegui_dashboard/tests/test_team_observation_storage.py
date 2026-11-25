import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from result_service.dashboard_service.common import decode
from nicegui_dashboard.team_bundle import SCHEMA, CHANNEL_SCHEMA, packed
from nicegui_dashboard.team_forecast_backfill import _same_observation, compare_recordings
from nicegui_dashboard.team_observation_storage import FIELDS, observation_schema, storage_row


class ObservationStorageTests(unittest.TestCase):
    def test_backfill_checks_the_observation_schema_and_detects_changed_fields(self):
        row = dict(observed_at_s=.5, available_at_s=.5, observer_id="s", peer_id="r",
                   values={"beacon_sinr_db":-0.0}, valid={"beacon_sinr_db":True}, ages={"beacon_sinr_db":0.0})
        with tempfile.TemporaryDirectory() as tmp:
            old, compact = (Path(tmp) / name for name in ("old.sqlite", "new.sqlite"))
            with closing(sqlite3.connect(old)) as db, db:
                db.executescript(SCHEMA)
                db.execute("INSERT INTO observations VALUES(?,?,?,?)", (.5, "s", "r", packed(row)))
            with closing(sqlite3.connect(compact)) as db, db:
                db.executescript(CHANNEL_SCHEMA + observation_schema())
                db.execute("INSERT INTO observations(time_s,observer,peer,field_mask,scalars,original) VALUES(?,?,?,?,?,?)",
                           storage_row(.5, "s", "r", row, packed))
            compare_recordings(old, compact)
            with closing(sqlite3.connect(old)) as db, db:
                changed = dict(row, values={"beacon_sinr_db":0.0})
                db.execute("UPDATE observations SET payload=?", (packed(changed),))
            with self.assertRaisesRegex(ValueError, "observations"):
                compare_recordings(old, compact)
            with closing(sqlite3.connect(old)) as db, db:
                db.execute("UPDATE observations SET payload=?", (packed(row),))
                db.execute("ALTER TABLE observations ADD COLUMN separate_measurement REAL")
            with self.assertRaisesRegex(ValueError, "observations"):
                compare_recordings(old, compact)

    def test_legacy_and_compact_queries_preserve_every_observation_field(self):
        observer, peer = 'observer"1', "peer"
        with closing(sqlite3.connect(":memory:")) as db:
            db.executescript(observation_schema())
            db.execute("PRAGMA trusted_schema=OFF")
            def check(t, values, valid, ages, extra=None, fallback=False):
                row = dict(observed_at_s=t, available_at_s=t, observer_id=observer, peer_id=peer,
                           values=values, valid=valid, ages=ages, **(extra or {}))
                encoded = storage_row(t, observer, peer, row, packed)
                self.assertEqual(encoded[-1] is not None, fallback)
                db.execute("INSERT INTO observations(time_s,observer,peer,field_mask,scalars,original) VALUES(?,?,?,?,?,?)", encoded)
                saved, = db.execute("SELECT payload FROM observations WHERE observer=? AND peer=? AND time_s=?",
                                    (observer, peer, t)).fetchone()
                self.assertEqual(json.dumps(decode(saved), sort_keys=True), json.dumps(row, sort_keys=True))
                self.assertTrue(_same_observation((t, observer, peer, packed(row)), (t, observer, peer, saved)))
                changed = dict(row, observed_at_s=t + 1e-9)
                self.assertFalse(_same_observation((t, observer, peer, packed(changed)), (t, observer, peer, saved)))
            # Null ratio, signed zero, subnormal and closely related doubles.
            values = dict(zip(FIELDS, [-63.390502181964216, -0.0, -63.390502181964216, 5e-324, None]))
            ages = dict(zip(FIELDS, [0.05499999999999999] * 2 + [0.0] * 2 + [None]))
            check(.445, values, {k:v is not None for k,v in values.items()}, ages)
            check(1., {}, {}, {})
            check(2., {"future_metric":1.0}, {"future_metric":True}, {"future_metric":0.0}, fallback=True)
            check(3., {}, {}, {}, {"new_clock": "source"}, fallback=True)
            check(4., dict.fromkeys(FIELDS[:2], None), dict.fromkeys(FIELDS[:2], False),
                  dict(zip(FIELDS[:2], [None, 0.0])), fallback=True)
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone(), ("ok",))
