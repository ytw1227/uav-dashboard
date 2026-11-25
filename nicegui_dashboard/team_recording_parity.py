"""Exact existing-channel equivalence for supplemental flight records."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from result_service.dashboard_contract.validation import read_json
from .team_bundle import make_scene, totals, validate_capture, write_database
from .team_forecast_backfill import compare_recordings


def verify_replay(bundle, manifest, row, arrays, scene):
    outages = validate_capture(row, arrays)
    fields = dict(team_commit=row["team_commit"], model_sha256=row["model_sha256"],
                  map_hash=row["map_sha256"], controller=row["controller"],
                  decision_interval_s=row["decision_interval_s"], final_metrics=totals(row),
                  simulation_end_s=row["duration_s"])
    if (row["spec"] != manifest["source_identity"]["spec"]
            or any(manifest.get(k) != v for k, v in fields.items())
            or make_scene(scene, row) != read_json(bundle / "scene.json")):
        raise ValueError("관측 비행의 조건·모델·지도 또는 지표가 원 비행과 다릅니다. 연결하지 않았습니다.")
    with tempfile.TemporaryDirectory(prefix="uav-operations-parity-") as temp:
        replay = Path(temp) / "data.sqlite"
        write_database(replay, row, arrays, manifest["fleet"], outages, manifest["metric_definitions"],
                       compact_observations=True)
        compare_recordings(bundle / "data.sqlite", replay)
        # Cause explanations are part of the existing viewing functionality too.
        with (closing(sqlite3.connect((bundle / "data.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)) as old,
              closing(sqlite3.connect(replay)) as new):
            if old.execute("SELECT * FROM incident_analysis ORDER BY time_s").fetchall() != new.execute(
                    "SELECT * FROM incident_analysis ORDER BY time_s").fetchall():
                raise ValueError("원 비행의 사건 분석과 관측 비행의 기록이 다릅니다.")


