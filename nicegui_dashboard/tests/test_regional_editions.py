import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nicegui_dashboard.library_view import Flight, LibraryView, MapCard, edition_counts, filter_cards
from nicegui_dashboard.team_region_view import RegionalRuns, applied_pin


def archive(region, pdr):
    return MapCard(region, region, "서울", "", {
        mode: Flight(region + mode, "archive", {}, {"pdr": pdr}) for mode in ("v21", "nominal")
    }, {"events":31, "hits":16, "recall":16/31})


class EditionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.card = archive("상암", .9471)
        self.library = LibraryView.__new__(LibraryView)
        self.library.archived = [self.card, archive("구월", .9099)]
        self.library.cards = self.library.archived[:]
        self.library.by_flight = {f.key:c for c in self.library.cards for f in c.flights.values()}
        self.library._render = MagicMock()
        self.library.mode = SimpleNamespace(value="v21")
        self.history = []
        self.updates = SimpleNamespace(history=lambda region: self.history[:], current=lambda region: self.history[0])
        self.runs = RegionalRuns(self.updates, self.library, "http://127.0.0.1:8765",
                                 SimpleNamespace(commit="a"*40))
        self.runs.selection = self.card.flights["v21"].key
        self.runs.versions, self.runs.archive = MagicMock(), MagicMock()
        self.runs.source_note, self.runs.set_pending = MagicMock(), MagicMock()
        self.runs.replay = MagicMock()
        self.runs.replay.open_team = AsyncMock(side_effect=self.open_team)
        self.runs.replay.select_run = AsyncMock(side_effect=self.open_archive)
        self.runs.update_button = MagicMock()
        self.add("old", .9572)
        native = patch("nicegui_dashboard.team_region_view.native_card_record", self.measured)
        native.start()
        self.addCleanup(native.stop)

    def add(self, name, pdr):
        self.history.insert(0, {"sha": name*10, "flights":{"v21":{"run_id":"team-"+name,"revision":"rev-"+name}},
                                "pdr":pdr})

    def measured(self, store, record):
        pin = applied_pin(record)
        return {"sha":record["sha"], "pin":pin, "metrics":{"pdr":record["pdr"]},
                "nominal":{"pdr":.9494}, "nominal_pin":("nominal-"+pin[0][5:], pin[1]),
                "nominal_replay": True, "evaluation":{"events":2, "hits":1, "recall":.5}}, []

    def open_team(self, region, pin, sha, mode, ticket, restart):
        rid, rev = pin
        return {"region":region,"mode":mode,"run_id":rid if mode == "v21" else "nominal-"+rid[5:],
                "revision":rev,"ticket":ticket,"team_context":rid, "team_interval":.5}

    def open_archive(self, rid, rev, ticket):
        return {"region":self.card.region,"mode":"v21","run_id":rid,"revision":rev,"ticket":ticket}

    async def test_selected_edition_drives_card_filter_recall_and_replay_together(self):
        await self.runs.show_selection()
        displayed = self.library.cards[0]
        self.assertEqual(displayed.ratio("v21","pdr"), .9572)
        self.assertEqual(displayed.recall, .5)
        self.assertFalse(filter_cards([displayed], metric="pdr").visible)
        await self.runs.choose_version(SimpleNamespace(value="archive"))
        self.assertEqual(self.library.cards[0].ratio("v21","pdr"), .9471)
        self.assertEqual(self.library.cards[0].recall, 16/31)
        self.assertEqual(filter_cards([self.library.cards[0]],metric="pdr").visible,[self.card])
        self.runs.replay.select_run.assert_awaited_once_with(*self.card.flights["v21"].key,ticket=2)

        self.add("new", .9999)
        await self.runs.show_selection()
        self.assertEqual(self.runs.display_choice, "archive")
        await self.runs.choose_version(SimpleNamespace(value="team-old/rev-old"))
        self.assertEqual(self.library.cards[0].ratio("v21","pdr"), .9572)
        self.runs.selection = self.card.flights["nominal"].key
        await self.runs.show_selection()
        self.assertEqual(self.runs.display_key, (self.runs.selection, "team-old/rev-old"))
        self.assertIn("기본 대형 · 실행한 팀 코드", self.runs.source_note.set_text.call_args[0][0])

    async def test_manual_analysis_keeps_edition_as_new_completion_arrives(self):
        await self.runs.show_selection()
        state = self.open_team("상암", ("team-old","rev-old"), "old"*10, "v21", 1, False)
        self.runs.replay_state({**state,"manual":True,"time":2845.5,"event_id":"chosen"})
        self.add("new", .9999)
        await self.runs.show_selection()
        self.runs.replay.open_team.assert_awaited_once()
        self.assertEqual(self.runs.display_choice, "team-old/rev-old")

    async def test_cancel_targets_only_the_displayed_batch_and_leaves_selected_flight_intact(self):
        self.updates.cancel = MagicMock()
        self.runs.refresh = AsyncMock()
        self.runs.batch_seen = {"id":4, "sha":"old", "active":True}
        self.runs.requested_display = ("상암","old")
        selected = self.runs.selection
        await self.runs.cancel_batch()
        self.updates.cancel.assert_called_once_with(4)
        self.assertIsNone(self.runs.requested_display)
        self.assertEqual(self.runs.selection, selected)
        self.runs.replay.open_team.assert_not_awaited()
        self.runs.batch_seen.update(cancelling=1)
        await self.runs.cancel_batch()
        self.updates.cancel.assert_called_once()

    async def test_automatic_playback_does_not_create_a_manual_hold(self):
        await self.runs.show_selection()
        state = self.open_team("상암", ("team-old","rev-old"), "old"*10, "v21", 1, False)
        self.runs.replay_state({**state,"playing":True,"time":17.5})
        self.add("new", .9999)
        await self.runs.show_selection()
        self.assertEqual(self.runs.display_choice, "team-new/rev-new")
        self.assertEqual(self.library.cards[0].ratio("v21","pdr"), .9999)

    async def test_summary_only_filtered_nominal_does_not_open_archival_replay(self):
        def summary_only(store, record):
            card, files = self.measured(store, record)
            card["nominal_replay"] = False
            card.pop("evaluation")
            return card, files
        with patch("nicegui_dashboard.team_region_view.native_card_record", summary_only):
            await self.runs.show_selection()
            card = self.library.cards[0]
            self.assertIsNone(card.recall)
            self.assertEqual(filter_cards([card],metric="pdr",mode="nominal").visible, [card])
            self.assertFalse(filter_cards([card],metric="pdr",mode="v21").visible)
            self.runs.selection = self.card.flights["nominal"].key
            self.runs.replay.open_team.side_effect = lambda *args: {"open_error":"기본 대형 경로 없음"}
            await self.runs.show_selection()
            self.runs.replay.select_run.assert_not_awaited()
            self.runs.archive.set_visibility.assert_called_with(False)
            self.assertIn("기본 대형 재생 기록 없음", self.runs.source_note.set_text.call_args[0][0])

            # Keep the filter and edition in place while its matching replay is published.
            self.runs.card_records[card.region]["nominal_replay"] = True
            self.runs.replay.open_team.side_effect = self.open_team
            await self.runs.show_selection()
            self.runs.replay.open_team.assert_awaited_with(
                card.region, ("team-old", "rev-old"), "old"*10, "nominal", 3, False)
            self.runs.archive.set_visibility.assert_called_with(True)
            self.runs.set_pending.assert_called_with(False)
            self.assertIn("기본 대형 · 실행한 팀 코드", self.runs.source_note.set_text.call_args[0][0])
            await self.runs.show_selection()
            self.assertEqual(self.runs.replay.open_team.await_count, 3)
            self.runs.replay.select_run.assert_not_awaited()

    async def test_unverifiable_selected_edition_clears_cards_and_hides_the_previous_flight(self):
        await self.runs.show_selection()
        self.add("new", .55)
        with patch("nicegui_dashboard.team_region_view.native_card_record", side_effect=ValueError("검사값 불일치")):
            await self.runs.show_selection()
        self.runs.replay.open_team.assert_awaited_once()
        self.assertIsNone(self.library.cards[0].ratio("v21", "pdr"))
        self.runs.replay.select_run.assert_not_awaited()
        self.runs.archive.set_visibility.assert_called_with(False)
        self.assertIn("검사값 불일치", self.runs.source_note.set_text.call_args[0][0])

    def test_filters_count_one_code_and_definition_at_a_time(self):
        cards = [self.card, replace(self.card, region="신규",team_sha="a"*40,team_comparison=True,
                                    metric_versions={"pdr":"p1"}),
                 replace(self.card, region="추가",team_sha="a"*40,team_comparison=True,
                         metric_versions={"pdr":"p2"})]
        groups = dict(edition_counts(cards, {"metric":"pdr"}))
        self.assertEqual(len(groups),3)
        self.assertTrue(all(g.counts["v21"] == g.valid == 1 for g in groups.values()))


if __name__ == "__main__":
    unittest.main()
