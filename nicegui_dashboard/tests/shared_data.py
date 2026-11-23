"""Read-only inputs for tests against the two original offline dashboards."""
import os
from pathlib import Path
from unittest import SkipTest

from nicegui_dashboard.__main__ import DATA


def shared_files():
    paths = {}
    for source, name in (("S", "상암DMC_대시보드.html"), ("B", "UAV_대시보드_v2.html")):
        variable = f"UAV_SHARED_{source}"
        configured = os.getenv(variable)
        path = Path(configured) if configured else DATA / name
        if not path.is_file():
            if configured:
                raise FileNotFoundError(f"{variable}: {path}")
            raise SkipTest(f"{name} 파일을 data/에 두거나 {variable} 경로를 지정하세요.")
        paths[source] = path
    return paths
