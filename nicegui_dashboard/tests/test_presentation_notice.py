import asyncio
import unittest
from unittest.mock import MagicMock

from nicegui_dashboard.registry_notice import PresentationNotice, RegistryFeed
from nicegui_dashboard.service import ServiceReadError


class PresentationNoticeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = MagicMock()
        self.feed = RegistryFeed(self.api)
        self.tabs = [PresentationNotice(self.feed), PresentationNotice(self.feed)]
        for tab in self.tabs:
            tab.label, tab.open_link, tab.dismiss_button = MagicMock(), MagicMock(), MagicMock()

    def publish(self, generation, *keys):
        self.api.status.return_value = {"generation": generation}
        self.api.runs.return_value = {"generation": generation, "runs": [
            {"run_id": rid, "bundle_revision": rev} for rid, rev in keys
        ]}
        self.feed.next_check = 0

    async def poll(self):
        await asyncio.gather(*(tab.refresh() for tab in self.tabs))

    async def test_team_completion_on_regional_page_points_to_existing_card(self):
        tab = self.tabs[0]
        tab.region_labels = {"hwajeong": "행신동 · 햇빛마을"}
        self.publish(1)
        await self.poll()
        self.publish(2, ("team-one", "rev-one"))
        self.api.runs.return_value["runs"][0]["metadata"] = {"region": "hwajeong", "adapter_id": "team-halfsec"}
        await self.poll()
        tab.label.set_text.assert_called_with("새 비행 1건 · 지역 카드에서 보기 (행신동 · 햇빛마을)")
        tab.open_link.set_visibility.assert_called_with(False)
        self.tabs[1].open_link.set_visibility.assert_called_with(True)

    async def test_two_tabs_share_status_and_keep_independent_acknowledgements(self):
        a, b = ("run-one","rev-one"), ("run-one","rev-two")
        self.publish(7, a)
        await self.poll()
        self.assertEqual(self.api.runs.call_count, 1)
        self.assertEqual(self.api.status.call_count, 1)
        self.assertFalse(any(t.unseen for t in self.tabs))

        self.publish(7, a)
        await self.poll()
        self.assertEqual(self.api.status.call_count, 2)
        self.assertEqual(self.api.runs.call_count, 1)
        self.publish(8, a, b)
        await self.poll()
        self.assertEqual(self.api.runs.call_count, 2)
        self.assertEqual(self.tabs[0].unseen, {b})
        self.assertEqual(self.tabs[1].unseen, {b})
        self.tabs[0].dismiss()
        self.assertFalse(self.tabs[0].unseen)
        self.assertEqual(self.tabs[1].unseen, {b})

    async def test_offline_first_and_reconnect_include_only_arrivals_since_known_list(self):
        self.api.status.side_effect = ServiceReadError("service_unavailable","test")
        await self.poll()
        self.tabs[0].label.set_text.assert_called_with("로컬 결과 서비스 연결 대기")
        a, b = ("run-one","rev-one"), ("run-two","rev-one")
        self.api.status.side_effect = None
        self.publish(1, a)
        await self.poll()
        self.assertFalse(self.tabs[0].unseen)
        self.api.status.side_effect = ServiceReadError("service_unavailable","test")
        self.feed.next_check = 0
        await self.poll()
        self.assertEqual(self.tabs[0].known, {a})
        self.api.status.side_effect = None
        # A restarted service can reuse a generation number.
        self.publish(1, a, b)
        await self.poll()
        self.assertEqual(self.tabs[0].unseen, {b})
