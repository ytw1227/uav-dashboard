"""Windows desktop shortcut for the local NiceGUI dashboard."""
import argparse
import ctypes
import http.client
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import time


PROJECT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT / "data/logs"
PORT = 8092


def dashboard_ready(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        try:
            connection.connect()
        except (ConnectionRefusedError, TimeoutError):
            return False
        connection.request("GET", "/part2/replay/S/margin_data.js")
        response = connection.getresponse()
        if (response.status == 200 and response.read(256).strip() ==
                b"export {marginRecords, warningThresholds} from '/part2/data/S/margins.js';"):
            return True
        raise RuntimeError(f"{port}번 포트를 다른 프로그램이 사용 중입니다.")
    finally:
        connection.close()


def ensure_running(port=PORT, results=None, log_dir=LOG_DIR):
    if dashboard_ready(port):
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    # Serialize double-clicks while the web server is still starting.
    with (log_dir / "startup.lock").open("a+b") as guard:
        deadline = time.monotonic() + 60
        while True:
            guard.seek(0)
            try:
                msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("앞서 실행한 대시보드가 아직 준비 중입니다.")
                time.sleep(0.3)
        if dashboard_ready(port):
            return
        log_file = log_dir / "startup.log"
        python = Path(sys.executable).with_name("python.exe")
        command = [str(python), "-B", "-m", "nicegui_dashboard", "app", "--port", str(port)]
        if results is not None:
            command.extend(["--data-root", str(results)])
        with log_file.open("ab") as log:
            process = subprocess.Popen(
                command,
                cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if dashboard_ready(port):
                return
            if process.poll() is not None:
                break
            time.sleep(0.4)
        raise RuntimeError(f"대시보드 시작을 확인할 수 없습니다.\n실행 기록: {log_file}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--service-project", type=Path)
    args = parser.parse_args()
    try:
        if args.service_project is not None:
            os.environ["UAV_SERVICE_PROJECT"] = str(args.service_project.resolve())
        ensure_running(results=args.data_root)
        os.startfile(f"http://127.0.0.1:{PORT}/regions")
    except (OSError, RuntimeError, http.client.HTTPException) as error:
        ctypes.windll.user32.MessageBoxW(None, str(error), "UAV 대시보드", 0x10)


if __name__ == "__main__":
    main()
