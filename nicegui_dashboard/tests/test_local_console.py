"""Operating-console behavior and comparison races."""
import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from nicegui_dashboard.local_view import Console, Operations, geography, groups_by_run, latest


class ConsoleTests(unittest.TestCase):
    def test_summary_and_conflict_are_never_flights(self):
        base = {"run_id":"run-12345678", "bundle_revision":"rev-1", "exported_at":"2026-01-01T00:00:00Z"}
        rows = [base, {**base,"bundle_revision":"rev-2","exported_at":"2026-01-02T00:00:00Z"},
                {**base,"run_id":"run-summary","artifact_kind":"evaluation_parent"},
                {**base,"run_id":"run-broken","conflict":True}]
        grouped = groups_by_run(rows)
        self.assertEqual(list(grouped), ["run-12345678"])
        self.assertEqual([r["bundle_revision"] for r in grouped["run-12345678"]], ["rev-2","rev-1"])

    def test_latest_requires_actual_completion(self):
        base = {"run_id":"run-12345678","bundle_revision":"rev-2","exported_at":"2026-11-20","stage_status":"complete"}
        known = {**base,"run_id":"run-complete","completed_at":"2026-01-02T00:00:00Z"}
        self.assertEqual(latest([base, known]), known)
        self.assertIsNone(latest([{**known, "registration_status":"pending"}, {**known, "stage_status":"failed"}, base]))

    def test_explicit_server_and_ownership_bounds(self):
        with self.assertRaises(ValueError):
            Operations("https://external.example.org")
        with self.assertRaises(ValueError):
            Operations().start_job("train", None)
        with self.assertRaises(ValueError):
            Operations().stop_job("../")
        self.assertEqual(geography().get("sangam_dmc"), "상암 DMC")

    def test_only_evaluate_receives_checkpoint_and_resource_limits(self):
        operations = Operations()
        with patch.object(operations, "_request") as request:
            for command in ("verify", "doctor"):
                operations.start_job(command, "left-over-from-evaluate.pt")
                request.assert_called_with("POST", "jobs", {"command": command})
            operations.start_job("evaluate", "model/selected.pt")
            request.assert_called_with("POST", "jobs", {
                "command": "evaluate", "checkpoint": "model/selected.pt", "cases": 1, "workers": 1,
            })


class ComparisonRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_snapshot_preserves_playback_time_pair_and_event(self):
        view = Console("http://127.0.0.1:8765")
        view.pin = ("run-12345678", "rev-12345678")
        view.last_query = (500, 1000, 620, "uav-0", "uav-4")
        view.event_value = "event-12"
        view.manifest = {"simulation_end_s": 1200}
        view.canvas = SimpleNamespace(id=71, client=SimpleNamespace(run_javascript=AsyncMock(return_value=710)))
        view.download = MagicMock()
        address = "/api/downloads/" + "a"*32 + ".html"
        view.api = SimpleNamespace(share=MagicMock(return_value={"download_url": address}))
        with patch("nicegui_dashboard.local_view.ui") as controls:
            await view.export("snapshot")
        view.api.share.assert_called_once_with({
            "format": "snapshot", "run_id": view.pin[0], "revision": view.pin[1], "window_s": [590,830],
            "time_s": 710, "selected_node": "uav-0", "selected_peer": "uav-4", "selected_event_id": "event-12",
        })
        controls.link.assert_called_once_with("590–830초 고정 스냅샷 받기", view.base+address, new_tab=True)

    async def race(self, update):
        # Run the actual callback twice. Halt the first worker before its response,
        # change NiceGUI's selection, then deliver the older worker's result last.
        waiting, release = threading.Event(), threading.Event()
        messages = []
        class Panel:
            def clear(self):
                messages.clear()
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
        def reply(pins, metric):
            key = (pins[0][1], metric)
            if key == ("rev-old", "pdr"):
                waiting.set()
                assert release.wait(5)
            counts = dict.fromkeys(("selected","included","mission_failure","process_failure_or_interrupted","data_error"), 0)
            return {"groups": [], "counts": counts, "excluded": [], "unverified_conditions": [],
                    "warnings": [f"result/{key[0]}/{key[1]}"]}
        view = Console("http://127.0.0.1:8765")
        view.api = SimpleNamespace(compare=reply)
        view.comparison = Panel()
        view.render_library = lambda: None
        view.versions = {"run-compare":[{"bundle_revision":"rev-old"},{"bundle_revision":"rev-new"}]}
        view.selected = {"run-compare":"rev-old"}
        view.metric = SimpleNamespace(value="pdr")
        with patch("nicegui_dashboard.local_view.ui", SimpleNamespace(label=messages.append)):
            first = asyncio.create_task(view.compare())
            try:
                self.assertTrue(await asyncio.to_thread(waiting.wait, 4))
                if update == "clear":
                    await view.clear_comparison()
                elif update == "revision":
                    await view.pick_revision("run-compare", "rev-new")
                else:
                    view.metric.value = "search_connected_mean"
                    await view.compare()
                expected = list(messages)
                self.assertNotIn("result/rev-old/pdr", expected)
            finally:
                release.set()
                await first
        self.assertEqual(messages, expected)
        return messages

    async def test_deselect_while_compare_in_flight(self):
        self.assertIn("보관 비행에서 비교할 실행을 선택하세요.", await self.race("clear"))

    async def test_revision_replacement_while_compare_in_flight(self):
        self.assertIn("result/rev-new/pdr", await self.race("revision"))

    async def test_metric_replacement_while_compare_in_flight(self):
        self.assertIn("result/rev-old/search_connected_mean", await self.race("metric"))
