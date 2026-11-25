"""A missing measurement does not define a new experimental condition."""
import unittest
from unittest.mock import patch

from result_service.dashboard_service.query import compare


class ComparisonMissingTests(unittest.TestCase):
    def setUp(self):
        self.records = {}

    def add(self, rid, value, *, model="model", definition="ordinary-pdr"):
        self.records[rid] = dict(
            run_id=rid, bundle_revision="rev", artifact_kind="episode", stage_status="completed",
            region="sangam", map_hash="scene", kind="continuous", direction="east_west", split="test",
            mode="v21", model_id=model, research_version="v1", config_hash="config",
            metric_definitions={"pdr": {"definition_hash":definition, "unit":"ratio",
                                        "preferred_direction":"higher"}} if definition is not None else {},
            final_metrics={} if value is None else {"pdr": value},
        )

    def list(self):
        return {"runs": []}

    def get(self, run_id, revision):
        return self.records[run_id], None

    def compare(self, keys=None):
        return compare(self, [{"run_id":k,"revision":"rev"} for k in self.records], keys)

    def test_finite_zero_and_missing_share_group_for_explicit_and_default_metrics(self):
        self.add("zero", 0)
        self.add("good", .75)
        self.add("missing", None)
        self.add("invalid", "not recorded")
        self.add("other_model", .25, model="other")
        self.add("other_definition", .4, definition="timely-pdr")
        self.add("unknown_definition", None, definition=None)
        for keys in (["pdr"], None):
            with self.subTest(keys=keys):
                result = self.compare(keys)
                self.assertEqual(len(result["groups"]), 4)
                group = next(g for g in result["groups"] if "zero" in g["run_ids"])
                stat, = group["metrics"]
                self.assertEqual((group["selected"], stat["selected"], stat["n"], stat["missing"]), (4,4,2,2))
                self.assertEqual((stat["values"],stat["mean"],stat["worst"]), ([0,.75],.375,0))
                rows = {r["run_id"]:r for r in result["runs"]}
                self.assertIn("기록되지", rows["missing"]["missing_metrics"]["pdr"])
                self.assertIn("유효한 수치", rows["invalid"]["missing_metrics"]["pdr"])
                self.assertIn("지표 정의", rows["unknown_definition"]["missing_metrics"]["pdr"])
                self.assertEqual(rows["zero"]["missing_metrics"], {})

    def test_all_missing_keeps_declared_definition_without_inventing_statistics(self):
        self.add("a", None)
        self.add("b", None)
        for keys in (["pdr"], None):
            with self.subTest(keys=keys):
                group, = self.compare(keys)["groups"]
                stat, = group["metrics"]
                self.assertEqual((stat["selected"],stat["n"],stat["missing"],stat["values"]), (2,0,2,[]))
                self.assertEqual(stat["definition"]["definition_hash"], "ordinary-pdr")
                self.assertTrue(all(stat[key] is None for key in ("mean","std","min","max","median","worst")))

    def test_team_ctr_label_preserves_revision_definition_and_real_missing_reason(self):
        from nicegui_dashboard.local_view import Operations

        for name in ("measured", "missing"):
            self.add(name, None)
            self.records[name]["metric_definitions"] = {"ctr": {
                "id": "team-halfsec.ctr", "unit": "ratio", "scope": "run_total", "definition_hash": "actual",
            }}
        self.records["measured"]["final_metrics"]["ctr"] = 0.8
        api = Operations()
        def request(method, path, body):
            return compare(self, body["runs"], body["metric_ids"])

        pins = [(name, "rev") for name in self.records]
        with patch.object(api, "_request", side_effect=request):
            response = api.compare(pins, "search_connected_mean")
        stat, = response["groups"][0]["metrics"]
        self.assertEqual((stat["metric_id"], stat["definition"]["definition_hash"], stat["values"]),
                         ("search_connected_mean", "actual", [.8]))
        rows = {row["run_id"]: row for row in response["runs"]}
        self.assertIn("기록되지", rows["missing"]["missing_metrics"]["search_connected_mean"])
        self.assertEqual([(row["run_id"], row["bundle_revision"]) for row in response["runs"]], pins)
