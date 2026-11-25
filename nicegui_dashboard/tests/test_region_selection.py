"""The selected presentation set must not edit scientific records or orphan pairs."""
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nicegui_dashboard.__main__ import BASELINE, CODECS
from nicegui_dashboard.library_view import geography, map_cards, filter_cards
from nicegui_dashboard.package_web import PackageMedia
from nicegui_dashboard.region_selection import (
    failed_presentation_regions, presentation_batch_status, presentation_catalog, region_selection,
)
from nicegui_dashboard.team_region_view import RegionalRuns
from nicegui_dashboard.tests.shared_data import shared_files


class PresentationSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = PackageMedia(shared_files(), CODECS, BASELINE)
        cls.original = cls.media.package("B").catalog
        cls.before = json.dumps(cls.original, sort_keys=True)
        cls.selected = set(region_selection()["selected_regions"])
        cls.visible = cls.media.catalog("B")

    @classmethod
    def tearDownClass(cls):
        cls.media.close()

    def test_all_navigation_indexes_match_the_same_fifty(self):
        catalog = self.visible
        self.assertEqual(len(self.selected), 50)
        self.assertEqual(set(catalog["scope"]["maps"]), self.selected)
        self.assertEqual(set(catalog["scope"]["flight_pairs"]), self.selected)
        self.assertEqual(set(catalog["dashboard_analysis"]["evaluations"]), self.selected)
        self.assertEqual(set(catalog["visualization"]["maps"]), self.selected)
        for key in ("incident_replay", "forecast_evaluation"):
            self.assertEqual(set(catalog["scope"][key]["regions"]), self.selected)
        self.assertEqual(set(catalog["scope"]["incident_replay"]["counts"]), self.selected)
        ids = set(catalog["entries"])
        self.assertEqual(len(ids), 100)
        self.assertEqual({row["run_id"] for row in catalog["runs"]}, ids)
        self.assertEqual(set(catalog["dashboard_analysis"]["incidents"]), ids)
        self.assertTrue(all(key.split("::", 1)[0] in ids for key in catalog["comparisons"]))
        for region, pair in catalog["scope"]["flight_pairs"].items():
            self.assertEqual(set(pair), {"nominal", "v21"})
            for rid in pair.values():
                self.assertEqual(catalog["entries"][rid], self.original["entries"][rid])
                self.assertEqual(catalog["entries"][rid]["manifest"]["region"], region)

    def test_original_seventy_and_sangam_are_preserved(self):
        self.assertEqual(len(self.original["scope"]["maps"]), 70)
        self.assertEqual(len(self.original["entries"]), 140)
        self.assertEqual(json.dumps(self.original, sort_keys=True), self.before)
        s = self.media.package("S").catalog
        self.assertIs(self.media.catalog("S"), s)
        self.assertEqual(self.media.package("B").file_digest(), BASELINE["B"])

    def test_cards_search_counts_and_whole_run_scope_use_selected_set(self):
        cards = map_cards(self.visible, geography("B"))
        self.assertEqual({card.region for card in cards}, self.selected)
        self.assertEqual(len(cards), 50)
        self.assertEqual(sum(len(card.flights) for card in cards), 100)
        self.assertEqual(cards[0].region, "bundang")
        for removed in region_selection()["excluded_regions"]:
            self.assertEqual(filter_cards(cards, search=removed).visible, [])
        for mode in ("nominal", "v21"):
            filtered = filter_cards(cards, mode=mode, metric="pdr")
            self.assertEqual(filtered.valid, 50)
            self.assertTrue({c.region for c in filtered.visible} <= self.selected)
        runs = RegionalRuns(None, SimpleNamespace(cards=cards), "", SimpleNamespace())
        self.assertEqual(set(runs.labels), self.selected)

    def test_missing_selected_archive_is_not_silently_ignored(self):
        catalog = {**self.original, "scope": {**self.original["scope"], "maps": ["bundang"]}}
        with self.assertRaises(ValueError):
            presentation_catalog(catalog, "B")


class SelectedRetryTests(unittest.IsolatedAsyncioTestCase):
    def test_old_batch_counts_and_rows_only_show_selected_regions(self):
        batch = {"id": 19, "sha": "a" * 40, "active": True, "region": "gangnam",
                 "completed": 2, "running": 1, "failed": 1, "total": 4, "items": [
            {"region": "bundang", "state": "completed"},
            {"region": "sangam_dmc", "state": "failed"},
            {"region": "gangnam", "state": "running"},
            {"region": "hwajeong", "state": "completed"},
        ]}
        before = deepcopy(batch)
        shown = presentation_batch_status(batch, {"bundang": "분당", "sangam_dmc": "상암"})
        self.assertEqual((shown["total"], shown["completed"], shown["running"], shown["failed"]), (2, 1, 0, 1))
        self.assertIsNone(shown["region"])
        self.assertNotIn("gangnam", json.dumps(shown))
        self.assertNotIn("hwajeong", json.dumps(shown))
        self.assertEqual(batch, before)
        self.assertEqual(shown["id"], batch["id"])
        self.assertTrue(shown["active"])

    async def test_retry_only_enqueues_visible_failed_regions(self):
        batch = {"id": 12, "sha": "a" * 40, "active": False, "items": [
            {"region": "bundang", "state": "failed"},
            {"region": "gangnam", "state": "failed"},
            {"region": "sangam_dmc", "state": "completed"},
        ]}
        labels = {"bundang": "분당", "sangam_dmc": "상암 DMC"}
        self.assertEqual(failed_presentation_regions(batch, labels), ["bundang"])
        run = RegionalRuns.__new__(RegionalRuns)
        run.updates = SimpleNamespace(start_many=MagicMock())
        run.code_updates = SimpleNamespace(syncing=False)
        run.coverage = {key: {"eligible": True} for key in labels}
        run.labels, run.busy, run.user_selection, run.batch_seen = labels, False, 0, deepcopy(batch)
        run.selection = ("selected", "rev")
        run.library = SimpleNamespace(by_flight={run.selection: SimpleNamespace(region="bundang")})
        run.update_button, run.refresh = MagicMock(), AsyncMock()
        await run.retry_failed()
        run.updates.start_many.assert_called_once_with(["bundang"], "a" * 40)
        self.assertEqual(run.requested_display, ("bundang", "a" * 40))


if __name__ == "__main__":
    unittest.main()
