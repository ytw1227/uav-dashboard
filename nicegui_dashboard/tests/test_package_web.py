import gzip
import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nicegui_dashboard.__main__ import BASELINE, CODECS
from nicegui_dashboard.package_web import PackageMedia, register_package_routes
from nicegui_dashboard.region_selection import region_selection
from nicegui_dashboard.tests.shared_data import shared_files


class PackageRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = PackageMedia(shared_files(), CODECS, BASELINE)
        api = FastAPI()
        register_package_routes(api, cls.media)
        cls.http = TestClient(api)

    @classmethod
    def tearDownClass(cls):
        cls.http.close()
        cls.media.close()

    def test_entire_catalog_and_lazy_manifests(self):
        for source, count in (("S", 1), ("B", len(region_selection()["selected_regions"]))):
            package = self.media.package(source)
            root = f"/part2/data/{source}"
            rows = self.http.get(root + "/catalog").json()
            self.assertEqual(len(rows["entries"]), 2 * count)
            self.assertEqual(len(rows["scope"]["maps"]), count)
            for region, pair in rows["scope"]["flight_pairs"].items():
                self.assertEqual(set(pair), {"v21", "nominal"})
                for rid in pair.values():
                    item = rows["entries"][rid]
                    full = package.entry(rid, item["revision"])
                    self.assertEqual(item["manifest"]["fleet"], full["manifest"]["fleet"])
                    for chunk in (item["scene_chunk"], item["events_chunk"], item["overview_chunk"], *item["observations"].values()):
                        self.assertTrue(package.has_chunk(chunk), chunk)
                    desc = rows["dashboard_analysis"]["incidents"][rid]
                    self.assertEqual(desc["revision"], item["revision"])
                    for key, value in desc.items():
                        if key.endswith("_chunk"):
                            self.assertTrue(package.has_chunk(value), value)
                ev = rows["dashboard_analysis"]["evaluations"][region]
                self.assertTrue(package.has_chunk(ev["chunk"]))
            rid = rows["scope"]["flight_pairs"]["sangam_dmc"]["v21"]
            rev = rows["entries"][rid]["revision"]
            self.assertEqual(self.http.get(f"{root}/manifest/{rid}/{rev}").json(), package.entry(rid, rev)["manifest"])
            self.assertEqual(self.http.get(f"{root}/manifest/{rid}/wrong-revision").status_code, 404)

    def test_presentation_catalog_revalidates_but_recordings_stay_cacheable(self):
        response = self.http.get("/part2/data/B/catalog")
        self.assertEqual(response.headers["cache-control"], "private, no-cache")
        self.assertEqual(self.http.get("/part2/data/S/catalog").headers["cache-control"],
                         "private, max-age=3600")
        entry = next(iter(self.media.catalog("B")["entries"].values()))
        response = self.http.get("/part2/data/B/packed/" + entry["scene_chunk"])
        self.assertEqual(response.headers["cache-control"], "private, max-age=3600")

    def test_packed_scene_and_conditional_margins(self):
        s = self.media.package("S")
        # One of B's three legacy margin sidecars belongs to an excluded region.
        for source, regions in (("S", 1), ("B", 2)):
            store = self.media.package(source)
            module = self.media.margin_module(source)
            records = json.loads(module.split("export const marginRecords = ", 1)[1][:-1])
            self.assertEqual(len(records), regions * 2)
            self.assertTrue(set(records) <= set(self.media.catalog(source)["entries"]))
            for rid, record in records.items():
                self.assertEqual(record["revision"], store.catalog["entries"][rid]["revision"])
            for rid, entry in s.catalog["entries"].items():
                self.assertEqual(records[rid], s.margin(rid, entry["revision"]))
            rid = store.catalog["scope"]["flight_pairs"]["sangam_dmc"]["v21"]
            item = store.catalog["entries"][rid]
            chunk = item["scene_chunk"]
            response = self.http.get(f"/part2/data/{source}/packed/{chunk}")
            codec, raw = store.packed_chunk(chunk)
            self.assertEqual(response.headers["x-uav-codec"], codec)
            self.assertEqual(response.content, gzip.decompress(raw))
        self.assertEqual(self.http.get("/part2/data/S/packed/missing").status_code, 404)
        self.assertEqual(self.http.get("/part2/data/L/catalog").status_code, 404)

    def test_sangam_incident_13_in_both_recordings(self):
        for source in ("S", "B"):
            with self.subTest(source=source):
                package = self.media.package(source)
                catalog = package.catalog
                run_id = catalog["scope"]["flight_pairs"]["sangam_dmc"]["v21"]
                descriptor = catalog["dashboard_analysis"]["incidents"][run_id]
                incidents = package.read_chunk(descriptor["events_chunk"])["events"]
                self.assertEqual(len(incidents), 20)
                self.assertEqual((incidents[12]["start_s"], incidents[12]["end_s"]), (2845.5, 2889))


if __name__ == "__main__":
    unittest.main()
