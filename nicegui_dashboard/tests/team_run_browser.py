"""Offline browser integration with a synthetic result and a simulated job queue."""
import asyncio
from pathlib import Path
import socket
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from unittest.mock import patch

from nicegui import app

from nicegui_dashboard.__main__ import main as dashboard
from nicegui_dashboard.managed_service import SERVICE_PROJECT
from nicegui_dashboard import team_region_view, team_update


def main():
    sys.path.insert(0, str(SERVICE_PROJECT))
    from dashboard_tests.fixtures import make_bundle, seal

    with TemporaryDirectory(prefix="team-region-browser-") as scratch:
        store = Path(scratch) / "results"
        bundle, manifest = make_bundle(store)
        sha = "a" * 40
        checkout = {"commit":sha, "assets":True, "starts":0}
        manifest.update(region="bundang", team_commit=sha, decision_interval_s=0.5)
        seal(bundle, manifest)
        result = {"region":"bundang", "sha":sha, "finished_at":"2026-01-01T00:00:00Z",
                  "flights":{"v21":{"run_id":manifest["run_id"], "revision":manifest["bundle_revision"]}}}

        class Queue:
            def __init__(self, output_root, *, max_output_gib):
                self.published = False
                self.work = None
                self.invocations = []

            def batch(self):
                return self.work

            def coverage(self, commit, *, refresh=False):
                from nicegui_dashboard.library_view import geography
                assert refresh and commit == checkout["commit"]
                if not checkout["assets"]:
                    raise ValueError("테스트 지도 자료 필요")
                return {r["region"]: dict(eligible=r["region"] != "sangam_dmc", reason="검증용 배제 지역")
                        for r in geography("B")}

            def current(self, region):
                return result if self.published and region == "bundang" else None

            def history(self, region):
                return [result] if self.current(region) else []

            def start_many(self, regions, commit):
                assert commit == checkout["commit"]
                checkout["starts"] += 1
                self.invocations.append(regions)
                if len(regions) == 1:
                    assert regions == ["bundang"]
                    self.published = True
                    state = "completed"
                else:
                    assert len(regions) == len(set(regions)) == 70
                    state = "failed"
                self.work = dict(sha=commit, total=len(regions), queued=0, running=0,
                                 active=False, region=None, completed=0, failed=0)
                self.work[state] = len(regions)
                self.work["items"] = [dict(region=r, state=state, message="브라우저 검증용") for r in regions]
                if len(regions) == 70:
                    self.work["failed"] = 69
                    self.work["unavailable"] = 1
                    next(i for i in self.work["items"] if i["region"] == "sangam_dmc")["state"] = "unavailable"

            def retry(self):
                assert len(self.invocations) == 2 and self.work["failed"] == 69
                self.work = dict(self.work, queued=69, failed=0, active=True,
                                 items=[dict(i, state="queued") if i["state"] == "failed" else i for i in self.work["items"]])

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        result_code = 1

        @app.post("/qa/external-checkout")
        def change_checkout():
            checkout.update(commit="b"*40, assets=False)
            return checkout

        @app.post("/qa/repair-assets")
        def repair_assets():
            checkout["assets"] = True
            return checkout

        def current_source(_):
            return {"commit":checkout["commit"]}

        def sync_source(path):
            time.sleep(0.7)
            return {**current_source(path), "changed":False}

        @app.on_startup
        async def verify():
            nonlocal result_code
            try:
                script = Path(__file__).with_name("team_run_browser.cjs")
                proc = await asyncio.to_thread(subprocess.run, ["node", str(script), f"http://127.0.0.1:{port}"], timeout=180)
                result_code = proc.returncode
            finally:
                app.shutdown()

        from nicegui_dashboard import team_source_view
        with (patch.object(team_update, "TeamUpdates", Queue),
              patch.object(team_source_view, "source_status", current_source),
              patch.object(team_source_view, "sync_source", sync_source),
              patch.object(team_region_view, "source_status", current_source),
              patch.object(sys, "argv", ["dashboard","app","--port",str(port),"--data-root",str(store)])):
            dashboard()
        return result_code


if __name__ == "__main__":
    sys.exit(main())
