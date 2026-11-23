"""Use the real Console callbacks to exercise Part 3 polling and response races."""
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nicegui_dashboard.local_view import Console, ServiceReadError, revision


def entry(run, rev, completed="2026-11-01T12:00:00Z"):
    return {"run_id": run, "bundle_revision": rev, "artifact_kind": "episode",
            "stage_status": "completed", "export_status": "ready", "registration_status": "registered",
            "completed_at": completed, "exported_at": "2026-11-02T10:00:00Z"}


OLD = entry("run-previous", "rev-one")
NEW = entry("run-new-result", "rev-two", "2026-11-03T12:00:00Z")


class FollowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ui = patch("nicegui_dashboard.local_view.ui")
        self.ui = ui.start()
        self.addCleanup(ui.stop)
        self.console = c = Console("http://127.0.0.1:8765")
        c.scope = SimpleNamespace(value="all")
        c.search = SimpleNamespace(value="")
        for field in ("status","rescan_button","registry_info","registry_values","library","notice","load_status",
                      "follow_state","resume_button","summaries","jobs"):
            setattr(c, field, MagicMock())
        c.api = MagicMock()
        c.api.status.return_value = {"errors":[]}
        c.api.jobs.return_value = {}
        c.api.summaries.return_value = {}

    def list_results(self, *rows):
        self.console.api.runs.return_value = {"generation":len(rows), "runs":list(rows)}

    async def test_checking_first_comparison_holds_scene_before_switching_tabs(self):
        c = self.console
        c.follow = True
        c.pin = (OLD["run_id"], revision(OLD))
        c.open_run = AsyncMock()
        c.compare = AsyncMock()
        self.list_results(OLD)
        await c.refresh()
        await c.toggle_compare(OLD, True)
        self.list_results(OLD, NEW)
        await c.refresh()
        self.assertFalse(c.following)
        c.open_run.assert_not_awaited()
        self.assertEqual(c.selected, {OLD["run_id"]: revision(OLD)})
        self.assertIn((NEW["run_id"], revision(NEW)), c.new_results)

    async def test_empty_first_poll_then_arrival_with_follow_off(self):
        c = self.console
        c.open_run = AsyncMock()
        self.list_results()
        await c.refresh()
        self.list_results(NEW)
        await c.refresh()
        self.assertIn((NEW["run_id"], revision(NEW)), c.new_results)
        self.assertEqual(c.follow_candidates, [NEW])
        self.assertFalse(c.following)
        c.open_run.assert_not_awaited()
        self.ui.button.assert_any_call("새 결과 보기", on_click=unittest.mock.ANY)

    async def test_eligible_completion_not_export_or_registration_order(self):
        c = self.console
        c.open_run = AsyncMock()
        newest_export = {**OLD, "exported_at":"2026-11-30T23:00:00Z"}
        unknown = entry("run-without-completion","rev-end",None)
        summary = {**NEW, "artifact_kind":"episode_summary", "run_id":"run-summary"}
        self.list_results(newest_export, unknown, summary, NEW)
        c.follow = True
        await c.refresh()
        c.open_run.assert_awaited_once_with(NEW, automatic=True)

    async def test_held_scene_and_new_revision_across_service_disconnection(self):
        c = self.console
        c.follow = True
        c.pin = (OLD["run_id"], revision(OLD))
        c.last_query = (0, 15, 7.5, "R1","S2")
        c.event_value = "event-kept"
        c.open_run = AsyncMock()
        self.list_results(OLD)
        await c.refresh()
        c.protect("사건 선택")
        c.api.runs.side_effect = ServiceReadError("service_unavailable", "연결 대기")
        await c.refresh()
        self.assertFalse(c.connected)
        c.api.runs.side_effect = None
        newer_revision = {**OLD, "bundle_revision":"rev-newer","exported_at":"2026-11-04T10:00:00Z"}
        self.list_results(OLD, newer_revision)
        await c.refresh()
        self.assertTrue(c.connected)
        self.assertEqual(c.pin, (OLD["run_id"], revision(OLD)))
        self.assertEqual(c.last_query, (0,15,7.5,"R1","S2"))
        self.assertEqual(c.event_value, "event-kept")
        self.assertEqual(c.new_results, {(OLD["run_id"], "rev-newer")})
        c.open_run.assert_not_awaited()
        await c.resume_follow()
        c.open_run.assert_awaited_once_with(newer_revision, automatic=True)

    async def test_presentation_has_to_be_released_then_follow_resumed(self):
        c = self.console
        c.follow_candidates = [NEW]
        c.open_run = AsyncMock()
        await c.change_follow(SimpleNamespace(value=True))
        c.open_run.reset_mock()
        c.change_presentation(SimpleNamespace(value=True))
        await c.apply_latest()
        await c.resume_follow()
        self.assertFalse(c.following)
        c.change_presentation(SimpleNamespace(value=False))
        self.assertFalse(c.following)
        c.open_run.assert_not_awaited()
        await c.resume_follow()
        c.open_run.assert_awaited_once_with(NEW, automatic=True)

    async def test_internal_playback_clock_and_user_play_command_are_distinct(self):
        c = self.console
        c.follow = True
        c.canvas = MagicMock()
        c.canvas.client.run_javascript = AsyncMock()
        c.clock_fields = [MagicMock() for _ in range(5)]
        c.query = AsyncMock()
        await c.canvas_time(SimpleNamespace(sender=c.canvas, args={"detail":{"t":7.5,"resume":True,"request_seq":0}}))
        self.assertTrue(c.following)
        self.assertEqual(c.clock_fields[2].value, 7.5)
        c.query.assert_awaited_once_with(resume=True)
        await c.map_command("toggle")
        self.assertFalse(c.following)
        self.assertEqual(c.hold_reason, "재생·일시정지")

    async def test_late_playback_boundary_cannot_rewind_a_manual_seek(self):
        c = self.console
        c.query_generation = 4
        c.clock_fields = [SimpleNamespace(value=v) for v in (0,20,17,"R1","S1")]
        c.canvas, c.query = MagicMock(), AsyncMock()
        await c.canvas_time(SimpleNamespace(sender=c.canvas, args={"detail":{"t":6,"resume":True,"request_seq":3}}))
        self.assertEqual(c.clock_fields[2].value, 17)
        c.query.assert_not_awaited()

    async def test_manual_input_during_automatic_flight_preparation(self):
        c = self.console
        c.follow = True
        c.pin = OLD["run_id"], revision(OLD)
        previous = c.manifest = dict(OLD)
        c.event_value, c.last_query = "event-old", (0,15,4.5,"S1","R2")
        entered, release = threading.Event(), threading.Event()

        def delayed_manifest(*_):
            entered.set()
            if not release.wait(4):
                raise TimeoutError("Test never released response")
            return dict(NEW)

        c.api.manifest.side_effect = delayed_manifest
        call = asyncio.create_task(c.open_run(NEW, automatic=True))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.assertEqual(c.pin, (OLD["run_id"], revision(OLD)))
            c.protect("사건 선택")
        finally:
            release.set()
            await call
        self.assertIs(c.manifest, previous)
        self.assertEqual(c.event_value, "event-old")
        self.assertEqual(c.last_query, (0,15,4.5,"S1","R2"))
        c.api.timeline.assert_not_called()

    async def test_wrong_initial_event_revision_keeps_previous_flight(self):
        c = self.console
        c.pin = OLD["run_id"], revision(OLD)
        c.manifest = dict(OLD)
        c.api.manifest.return_value = {**NEW, "map_hash":"sample-map", "simulation_end_s":17}
        c.api.scene.return_value = {"map_hash":"sample-map", "bounds":[0,0,10,10]}
        c.api.timeline.return_value = {**NEW, "request_id":"open-1"}
        c.api.events.return_value = {**NEW, "bundle_revision":"rev-unrelated"}
        await c.open_run(NEW)
        self.assertEqual(c.pin, (OLD["run_id"],revision(OLD)))
        self.assertEqual(c.manifest, OLD)
        self.assertIn("열 수 없습니다", c.load_status.set_text.call_args.args[0])

    async def test_older_range_and_pair_reply_cannot_overwrite_new_view(self):
        c = self.console
        c.pin = OLD["run_id"], revision(OLD)
        c.manifest = {**OLD, "simulation_end_s":50}
        c.clock_fields = [SimpleNamespace(value=v) for v in (0,15,0,"R1","S1")]
        c.report, c.series, c.canvas = MagicMock(), MagicMock(), MagicMock()
        c.canvas.client.run_javascript = AsyncMock(return_value=None)
        c.api.events.return_value = {**OLD, "total":0, "events":[]}
        entered, release = threading.Event(), threading.Event()

        def delay_pair(*_, request_id, peer, **kwargs):
            if peer == "S1":
                entered.set()
                if not release.wait(4):
                    raise TimeoutError("Test never released response")
            return {**OLD, "request_id":request_id}

        c.api.timeline.side_effect = delay_pair
        first = asyncio.create_task(c.query())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            c.clock_fields[-1].value = "S2"
            c.clock_fields[2].value = 2
            await c.query()
        finally:
            release.set()
            await first
        self.assertEqual(c.last_query, (0,15,2,"R1","S2"))
        c.canvas.client.run_javascript.assert_awaited_once()
