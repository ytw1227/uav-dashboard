"""Offline web regression. Run from the repository with python -m nicegui_dashboard.tests.team_sync_browser."""
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from nicegui import app

from nicegui_dashboard import team_source
from nicegui_dashboard.__main__ import main as dashboard


def main():
    with tempfile.TemporaryDirectory(prefix="dashboard-team-web-") as temp:
        root = Path(temp)
        origin = root / "team-origin"
        origin.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        checkout = root / "team-simulator"
        client = Path(__file__).with_name("team_sync_browser.cjs")
        git = team_source._git

        def git_with_latency(directory, *args):
            # Make the in-flight button state observable even on an in-memory Git fixture.
            if args[0] in ("clone", "fetch"):
                time.sleep(0.8)
            return git(directory, *args)

        result = 1

        @app.on_startup
        async def run_browser():
            nonlocal result
            try:
                proc = await asyncio.to_thread(subprocess.run, ["node", str(client), url,
                                                               str(origin), str(checkout)], timeout=180)
                result = proc.returncode
            finally:
                app.shutdown()

        args = ["dashboard", "app", "--port", str(port), "--data-root", str(root / "results")]
        with (patch.dict(os.environ, {"UAV_TEAM_SOURCE": str(checkout)}),
              patch.object(team_source, "TEAM_URL", str(origin)),
              patch.object(team_source, "_git", git_with_latency), patch.object(sys, "argv", args)):
            dashboard()
        return result


if __name__ == "__main__":
    sys.exit(main())
