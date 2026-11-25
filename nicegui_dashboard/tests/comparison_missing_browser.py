"""Exercise a valid and a missing result through the real comparison UI in a temporary store."""
import asyncio
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from nicegui import app
from nicegui_dashboard.__main__ import main as dashboard
from nicegui_dashboard.managed_service import SERVICE_PROJECT


def main():
    with patch.object(sys, "path", [str(SERVICE_PROJECT), *sys.path]):
        from dashboard_tests.fixtures import make_bundle, seal

    with TemporaryDirectory(prefix="uav-missing-metric-") as temp:
        store = Path(temp)
        first, _ = make_bundle(store, number=1)
        second, manifest = make_bundle(store, number=2)
        scene = (first / "scene.json").read_bytes()
        (second / "scene.json").write_bytes(scene)
        metadata = json.loads(scene)
        manifest.update(map_id=metadata["map_id"], map_hash=metadata["map_hash"])
        del manifest["final_metrics"]["pdr"]
        with sqlite3.connect(second / "data.sqlite") as db:
            db.execute("DELETE FROM metrics WHERE metric_id='pdr'")
        db.close()
        seal(second, manifest)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        result = 1

        @app.on_startup
        async def verify():
            nonlocal result
            try:
                script = Path(__file__).with_suffix(".cjs")
                process = await asyncio.to_thread(subprocess.run, [
                    "node", str(script), f"http://127.0.0.1:{port}/local?scope=all",
                ], timeout=100)
                result = process.returncode
            finally:
                app.shutdown()

        with patch.object(sys, "argv", ["dashboard", "app", "--data-root", str(store), "--port", str(port)]):
            dashboard()
        return result


if __name__ == "__main__":
    sys.exit(main())
