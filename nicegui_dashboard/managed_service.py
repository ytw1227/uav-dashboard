"""Run the original bundle registry beside NiceGUI for an explicit result folder."""
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from threading import Thread


SERVICE_PROJECT = Path(os.getenv("UAV_SERVICE_PROJECT") or
                       Path(__file__).resolve().parents[1] / "result_service").resolve()


@contextmanager
def serve_results(project: Path, output: Path, *, quota_gib: float):
    project = project.resolve()
    if not (project / "dashboard_service" / "registry.py").is_file():
        raise FileNotFoundError(f"결과 서비스 코드를 찾을 수 없습니다. {project} (UAV_SERVICE_PROJECT)")
    sys.path.insert(0, str(project))
    try:
        from dashboard_service.http import LocalServer
        from dashboard_service.registry import Registry

        registry = Registry(project, output.resolve(), interval_s=10, scan_raw=False,
                            max_output_gib=quota_gib)
        with LocalServer(("127.0.0.1", 0), registry) as service:
            worker = Thread(target=service.serve_forever, name="uav-result-service", daemon=True)
            worker.start()
            try:
                registry.start()
                yield f"http://127.0.0.1:{service.server_port}"
            finally:
                service.shutdown()
                worker.join()
                registry.close()
    finally:
        sys.path.remove(str(project))
