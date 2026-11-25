"""Recover a missing forecast channel by replaying and verifying the original sealed team flight."""
import argparse
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import zlib

from result_service.dashboard_contract.validation import confined, read_json, validate_bundle
from .team_bundle import digest, make_scene, totals, validate_capture, write_database
from .team_capture import capture, write_result
from .team_forecast_record import FLEET_FORECAST_HEAD, forecast_digest, forecast_path, load_forecast
from .team_runtime import file_lock, native_path


TABLES = ("positions", "rf_frames", "decisions", "observations", "events", "metrics")
HEAD = "fleet_onset_probability"


def compare_recordings(original_db, replay_db):
    # The same exporter writes both files. Compare ordered records so different
    # SQLite page layouts cannot masquerade as a difference in flight behaviour.
    with (closing(sqlite3.connect(original_db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as old,
          closing(sqlite3.connect(replay_db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as new):
        observation_columns = ("time_s", "observer", "peer", "payload")
        for db in (old, new):
            names = tuple(row[1] for row in db.execute("PRAGMA table_xinfo(observations)"))
            if names not in (observation_columns, observation_columns + ("field_mask", "scalars", "original")):
                raise ValueError("원 비행의 observations 기록 형식을 확인할 수 없습니다.")
        for table in TABLES:
            order = "time_s, observer, peer" if table == "observations" else "1"
            columns = "time_s,observer,peer,payload" if table == "observations" else "*"
            query = f"SELECT {columns} FROM {table} ORDER BY {order}"
            a, b = old.execute(query), new.execute(query)
            while True:
                left, right = a.fetchmany(256), b.fetchmany(256)
                if left != right:
                    # Only compact observations use text. The historic binary
                    # path continues to require its original byte-for-byte match.
                    if table != "observations" or len(left) != len(right) or not all(
                        _same_observation(x, y) for x,y in zip(left, right)
                    ):
                        raise ValueError(f"원 비행의 {table} 기록과 일치하지 않아 예측값을 연결하지 않았습니다.")
                if not left:
                    break


def _same_observation(left, right):
    if left == right:
        return True
    if left[:3] != right[:3] or not any(isinstance(row[3], str) for row in (left, right)):
        return False
    raw = [row[3] if isinstance(row[3], str) else zlib.decompress(row[3]).decode() for row in (left, right)]
    if raw[0] == raw[1]:
        return True
    # Dictionary order carries no research meaning. Compare exact JSON scalars,
    # retaining masks, nulls, types, timestamps, and signed zeros.
    return json.dumps(json.loads(raw[0]), sort_keys=True) == json.dumps(json.loads(raw[1]), sort_keys=True)


def recover_payload(bundle, manifest, row, arrays, scene):
    outages = validate_capture(row, arrays)
    if row.get("team_capture_version") not in {"2","3"} or HEAD not in arrays:
        raise ValueError("편대 모델의 원래 단절 예측 출력이 없습니다.")
    fleet = [dict(id=f"uav-{i}", role="search" if i < row["search_drones"] else "relay")
             for i in range(row["total_drones"])]
    identity = {"team_commit": row["team_commit"], "model_sha256": row["model_sha256"],
                "map_hash": row["map_sha256"], "controller": row["controller"],
                "decision_interval_s": row["decision_interval_s"], "fleet": fleet,
                "simulation_end_s": row["duration_s"], "final_metrics": totals(row)}
    if (manifest.get("adapter_id") != "team-halfsec" or manifest["source_identity"]["spec"] != row["spec"]
            or any(manifest.get(key) != value for key, value in identity.items())
            or make_scene(Path(scene), row) != read_json(bundle / "scene.json")):
        raise ValueError("지도·모델·기체·실행 조건 또는 임무 결과가 원 비행과 다릅니다.")

    with tempfile.TemporaryDirectory(prefix="uav-flight-forecast-") as temporary:
        replay_db = Path(temporary) / "data.sqlite"
        from .team_telemetry import legacy_capture_view
        prior_row,prior_channels = legacy_capture_view(row,arrays,False)
        write_database(replay_db, prior_row, prior_channels, fleet, outages, manifest["metric_definitions"])
        compare_recordings(bundle / "data.sqlite", replay_db)

    network = arrays["network"]
    probabilities = [None] * len(network)
    for frame, supported, probability in zip(arrays["plan_frames"], arrays["support_counts"], arrays[HEAD], strict=True):
        if supported > 0:
            probabilities[int(frame)] = float(probability)
    payload = dict(schema_version=1, run_id=manifest["run_id"], bundle_revision=manifest["bundle_revision"],
                   manifest_sha256=digest(bundle / "manifest.json"), data_sha256=manifest["artifacts"]["data.sqlite"]["sha256"],
                   team_commit=row["team_commit"], model_sha256=row["model_sha256"], map_hash=row["map_sha256"],
                   head_definition=FLEET_FORECAST_HEAD, probabilities=probabilities,
                   pre_action_connected=[None, *map(bool, network[:-1])])
    threshold = row.get("model_threshold")
    if type(threshold) in (int, float) and math.isfinite(threshold) and 0 <= threshold <= 1:
        if manifest.get("model_threshold", threshold) != threshold:
            raise ValueError("원 비행의 판단 기준과 복원한 모델이 다릅니다.")
        payload["model_threshold"] = threshold
    payload["payload_sha256"] = forecast_digest(payload)
    return payload


def recover_pin(output_root, rid, revision, capture_dir=None):
    root = Path(output_root).resolve()
    target = forecast_path(root, rid, revision)
    bundle = confined(root / "bundles", Path(rid) / revision, must_exist=False)
    if bundle != root / "bundles" / rid / revision or not bundle.is_dir():
        raise ValueError("원래 비행 기록 폴더를 확인할 수 없습니다.")
    with file_lock(target.with_suffix(".lock"), wait=True):
        manifest = validate_bundle(bundle)
        if (manifest["run_id"], manifest["bundle_revision"], manifest["adapter_id"]) != (rid, revision, "team-halfsec"):
            raise ValueError("선택한 결과와 실제 팀 비행 기록이 다릅니다.")
        if HEAD in manifest["head_definitions"]:
            return "already-recorded"
        manifest_hash = digest(bundle / "manifest.json")
        if target.exists():
            load_forecast(target, manifest, manifest_hash)
            return "already-recovered"
        if capture_dir:
            import numpy as np
            capture_dir = Path(capture_dir)
            row = read_json(capture_dir / "flight.json")
            scene = Path(read_json(capture_dir / "source.json")["scene"])
            with np.load(capture_dir / "frames.npz", allow_pickle=False) as recording:
                arrays = {k: recording[k] for k in recording.files}
        else:
            sha = manifest["team_commit"]
            if not re.fullmatch("[0-9a-f]{40}", sha):
                raise ValueError("팀 비행의 소스 버전을 확인할 수 없습니다.")
            versions = root.parent / "team-update" / "versions"
            snapshot = confined(versions, Path(sha) / "snapshot.json")
            core = Path(read_json(snapshot)["release_root"]).resolve()
            row, arrays, scene = capture(native_path(snapshot.parent / "code"), core, manifest["source_identity"]["spec"], sha)
        payload = recover_payload(bundle, manifest, row, arrays, scene)
        write_result(target, payload)
        return "recovered"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    select = parser.add_mutually_exclusive_group(required=True)
    select.add_argument("--region")
    select.add_argument("--pin", nargs=2, metavar=("RUN", "REVISION"))
    select.add_argument("--all", action="store_true")
    parser.add_argument("--capture-dir", type=Path, help="Reuse one completed native capture with the original forecast head")
    args = parser.parse_args()
    root = args.output_root.resolve()
    for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[k] = "1"
    if args.pin:
        print(recover_pin(root, *args.pin, capture_dir=args.capture_dir), flush=True)
        return
    pins = []
    for ready in sorted((root / "bundles").glob("team-*/*/READY")):
        bundle = ready.parent
        m = read_json(bundle / "manifest.json")
        if (args.all or m["region"] == args.region) and HEAD not in m.get("head_definitions", {}):
            pins.append((m["region"], bundle.parent.name, bundle.name))
    if args.capture_dir and len(pins) != 1:
        parser.error("Use --pin with a retained capture when multiple or zero eligible pins match.")
    failures = []
    for region, rid, rev in pins:
        print(json.dumps(dict(region=region, run_id=rid, revision=rev)), flush=True)
        # Each snapshot's imports, torch model and scene cache live in their own process.
        cmd = [sys.executable, "-B", "-m", "nicegui_dashboard.team_forecast_backfill",
               "--output-root", str(root), "--pin", rid, rev]
        if args.capture_dir:
            cmd.extend(["--capture-dir", str(args.capture_dir.resolve())])
        if subprocess.run(cmd).returncode:
            failures.append(region)
    if failures:
        raise SystemExit("Forecast recovery failed: " + ", ".join(failures))


if __name__ == "__main__":
    main()
