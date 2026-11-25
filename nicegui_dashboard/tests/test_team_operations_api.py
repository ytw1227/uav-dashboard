"""The operations route must use the selected store, never a bundle-relative store."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nicegui_dashboard.team_replay_data import register_team_replay_routes
from nicegui_dashboard.tests import test_team_operations as fixtures


class OperationsRouteTests(TestCase):
    def setUp(self):
        self.fixture = fixture = fixtures.OperationContractTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.manifest = dict(fixture.manifest, mode="v21", recorded_mode="v21_halfsec")
        bundle = fixture.root / "bundles" / self.manifest["run_id"] / self.manifest["bundle_revision"]
        # Bundle verification is tested independently; isolate routing and sidecar identity here.
        for name in ("READY", "scene.json", "data.sqlite"):
            (bundle / name).write_text("{}")
        self.checked = patch("nicegui_dashboard.team_replay_data.checked_record",
                             return_value=(self.manifest, {}, []))
        self.checked.start()
        self.addCleanup(self.checked.stop)
        app = FastAPI()
        register_team_replay_routes(app, fixture.root)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.url = f'/part2/team/{self.manifest["run_id"]}/{self.manifest["bundle_revision"]}/operations'

    def test_published_operations_are_found_from_store_root_and_do_not_modify_manifest(self):
        self.fixture.publish()
        before = deepcopy(self.manifest)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.fixture.payload)
        self.assertEqual(self.manifest, before)
        self.assertIn("no-cache", response.headers["cache-control"])

    def test_absence_or_wrong_identity_never_returns_another_flights_roles(self):
        self.assertEqual(self.client.get(self.url).json()["status"], "not_recorded")
        self.fixture.payload["identity"]["team_commit"] = "f" * 40
        self.fixture.seal()
        self.fixture.publish()
        with self.assertLogs("nicegui_dashboard.team_replay_data", level="WARNING"):
            value = self.client.get(self.url).json()
        self.assertEqual(value["status"], "error")
        self.assertNotIn("roles", value)
