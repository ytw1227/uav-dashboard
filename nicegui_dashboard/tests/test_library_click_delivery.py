"""Selection callbacks remain valid when a mode acknowledgement replaces cards."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from nicegui_dashboard.library_view import LibraryView


class LibraryClickDelivery(unittest.TestCase):
    def setUp(self):
        self.view = object.__new__(LibraryView)
        self.view.mode = SimpleNamespace(value="nominal")
        self.flights = {mode: SimpleNamespace(key=(mode + "-run", "revision"))
                        for mode in ("nominal", "v21")}
        self.view.cards = [SimpleNamespace(region="one", flights=self.flights)]
        self.view.on_open = Mock(return_value="opened")
        self.view.sync_selection = Mock()
        self.view.select_mode_for_result = Mock()

    def click(self, args):
        return self.view._open_region(SimpleNamespace(args=args))

    def test_delayed_click_uses_captured_mode_and_calls_open_once(self):
        self.assertEqual(self.click({"region": "one", "mode": "v21"}), "opened")
        self.view.select_mode_for_result.assert_called_once_with("one", "v21")
        self.view.on_open.assert_called_once_with("v21-run", "revision")
        self.view.sync_selection.assert_called_once_with("v21-run", "revision")

    def test_same_card_still_restarts_once_per_deliberate_click(self):
        self.click({"region": "one", "mode": "nominal"})
        self.click({"region": "one", "mode": "nominal"})
        self.assertEqual(self.view.on_open.call_count, 2)
        self.view.select_mode_for_result.assert_not_called()

    def test_replaced_card_resolves_current_catalog(self):
        self.view.cards[0].flights = dict(self.flights, v21=SimpleNamespace(key=("replacement", "new-revision")))
        self.click({"region": "one", "mode": "v21"})
        self.view.on_open.assert_called_once_with("replacement", "new-revision")

    def test_unknown_input_never_selects_a_flight(self):
        for payload in (None, [], {}, {"region": "missing", "mode": "v21"},
                        {"region": "one", "mode": "unsupported"}):
            with self.subTest(payload=payload):
                self.assertIsNone(self.click(payload))
        self.view.on_open.assert_not_called()
        self.view.select_mode_for_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
