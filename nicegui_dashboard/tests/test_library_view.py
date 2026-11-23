import unittest
from copy import deepcopy
from dataclasses import replace

from nicegui_dashboard.__main__ import CODECS
from nicegui_dashboard.library_view import filter_cards, geography, map_cards
from nicegui_dashboard.portable import PortableDashboard
from nicegui_dashboard.tests.shared_data import shared_files


class LibraryParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = {}
        for source, path in shared_files().items():
            with PortableDashboard(path, codec_dir=CODECS) as archive:
                cls.catalogs[source] = archive.catalog
        cls.s = map_cards(cls.catalogs["S"], geography("S"))
        cls.b = map_cards(cls.catalogs["B"], geography("B"))

    def test_map_and_flight_counts(self):
        for cards, n in ((self.s, 1), (self.b, 70)):
            self.assertEqual(len(cards), n)
            self.assertEqual(sum(len(c.flights) for c in cards), 2*n)
            self.assertEqual(len({c.region for c in cards}), n)

    def test_geography_search_and_empty_result(self):
        for term in (" 상암 ", "SANGAM"):
            result = filter_cards(self.b, search=term, province="서울특별시", district="마포구")
            self.assertEqual([c.region for c in result.visible], ["sangam_dmc"])
        self.assertFalse(filter_cards(self.b, search="no-map-with-this-id").visible)

    def test_threshold_and_sort(self):
        for mode, hit in (("v21", 14), ("nominal", 35)):
            result = filter_cards(self.b, mode=mode, metric="pdr")
            self.assertEqual(result.valid, 70)
            self.assertEqual(len(result.visible), hit)
            self.assertEqual(result.counts, {"nominal": 35, "v21": 14})
            self.assertEqual(len(result.above)-len(result.below), 21)
        self.assertEqual(filter_cards(self.b, order="recall_desc").visible[0].label, "석촌동 · 석촌호수")
        self.assertTrue(filter_cards(self.b, order="pdr_asc").visible[0].label.startswith("태평동"))
        self.assertEqual(self.s[0].recall, 16/31)

    def altered_record(self, change):
        # Copy only rows being changed. Each fixture starts with the same real pair.
        catalog = dict(self.catalogs["S"], runs=deepcopy(self.catalogs["S"]["runs"]))
        change(catalog["runs"])
        return map_cards(catalog, geography("S"))

    def test_sort_ties_and_missing_in_both_flight_modes(self):
        def card(region, label, value):
            template = self.s[0]
            flights = {key: replace(flight, metrics={"pdr": value})
                       for key, flight in template.flights.items()}
            return replace(template, region=region, label=label, flights=flights,
                           evaluation={"recall": value, "events": int(value is not None)})
        examples = [card("b", "가", .8), card("c", "나", .8), card("a", "가", .8),
                    card("d", "다", 0), card("e", "라", None)]
        for mode in ("nominal", "v21"):
            for metric in ("pdr", "recall"):
                for order, expected in (("asc", "dabce"), ("desc", "abcde")):
                    with self.subTest(mode=mode, metric=metric, order=order):
                        result = filter_cards(examples, mode=mode, order=f"{metric}_{order}")
                        self.assertEqual([c.region for c in result.visible], list(expected))

    def test_mismatch_never_creates_a_comparison(self):
        for key, value in (("paired_v21_revision", "wrong"), ("seed", 0), ("map_hash", "wrong")):
            with self.subTest(key=key):
                def mismatched(rows):
                    next(r for r in rows if r["metadata"]["mode"] == "nominal")["metadata"][key] = value
                records = self.altered_record(mismatched)
                self.assertEqual(len(records[0].flights), 1)
                result = filter_cards(records, metric="pdr")
                self.assertEqual(result.valid, 0)
                self.assertEqual(result.missing, ["상암 DMC"])

    def test_exact_boundary_missing_and_duplicate(self):
        def boundary(rows):
            for r in rows:
                r["metrics"]["pdr"] = .95
            rows.extend((rows[0], dict(rows[0], artifact_kind="evaluation_parent")))
        cards = self.altered_record(boundary)
        self.assertEqual(len(cards), 1)
        self.assertEqual(filter_cards(cards, metric="pdr").counts, {"v21": 1, "nominal": 1})
        def missing(rows):
            rows[0]["metrics"]["pdr"] = None
        result = filter_cards(self.altered_record(missing), metric="pdr")
        self.assertEqual(result.missing, ["상암 DMC"])
        self.assertEqual(result.visible, [])


if __name__ == "__main__":
    unittest.main()
