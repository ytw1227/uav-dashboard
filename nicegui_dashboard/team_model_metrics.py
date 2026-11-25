"""Same-flight control statistics and independent, pinned regional forecast evaluation."""
from copy import deepcopy
import hashlib
import inspect
import math
from pathlib import Path

from result_service.dashboard_contract.validation import canonical_hash, confined, read_json
from .team_baseline_record import pin_fields

CONTROL_KEYS = ("starts_per_h", "cut_seconds_per_h", "timely_pdr", "relay_m")
FORECAST_KEYS = ("recall", "precision", "false_per_h", "average_precision")


def control_identity(row):
    return dict(team_commit=row["team_commit"], map_hash=row["map_sha256"],
                model_sha256=row["model_sha256"], controller=row["controller"], spec=row["spec"])


def native_control(row, evaluator=None):
    """Call the pinned research aggregator without repeating its equations."""
    if evaluator is None:
        from fast_halfsec.evaluation import pooled
        evaluator = pooled
    summary = evaluator([row])
    source = Path(inspect.getsourcefile(evaluator))
    raw_keys = ("duration_s", "onsets", "cut_seconds", "generated", "timely", "timely_pdr",
                "relay_travel_m", "search_travel_m", "network_fraction", "mission_completed")
    return dict(schema_version=1, identity=control_identity(row), summary=summary,
                raw={key:row[key] for key in raw_keys},
                provenance=dict(module=evaluator.__module__, function=evaluator.__name__,
                                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                                flights=1, motion_sample_s=.1,
                                relay_distance="sum of all relay XY movement at native 0.1-second motion ticks; altitude travel excluded"))


def validate_control(control, manifest):
    expected = {key:manifest[key] for key in ("team_commit", "map_hash", "model_sha256", "controller")}
    expected["spec"] = manifest["source_identity"]["spec"]
    if control.get("schema_version") != 1 or control.get("identity") != expected:
        raise ValueError("제어 집계의 코드·모델·지도·조건이 선택한 비행과 다릅니다.")
    summary, raw = control["summary"], control["raw"]
    if (summary.get("flights") != 1 or raw.get("duration_s") != manifest["simulation_end_s"]
            or summary.get("hours") != raw["duration_s"]/3600.
            or summary.get("relay_m") != raw.get("relay_travel_m")
            or any(type(summary.get(key)) not in (int, float) or not math.isfinite(summary[key])
                   or summary[key] < 0 for key in CONTROL_KEYS)
            or not 0 <= summary["timely_pdr"] <= 1):
        raise ValueError("지역 제어 지표의 범위 또는 원본 이동거리와 집계가 다릅니다.")
    totals = manifest["final_metrics"]
    for raw_key, metric in (("onsets", "persistent_onsets"), ("cut_seconds", "persistent_cut_seconds"),
                            ("generated", "generated_packets"), ("timely_pdr", "timely_pdr")):
        if raw[raw_key] != totals[metric]:
            raise ValueError("지역 제어 집계가 선택한 비행의 기존 지표와 다릅니다.")
    provenance = control.get("provenance", {})
    if provenance.get("module") != "fast_halfsec.evaluation" or provenance.get("function") != "pooled":
        raise ValueError("기존 연구 평가 함수로 계산한 제어 집계가 아닙니다.")
    return control


def metrics_path(bundle):
    bundle = Path(bundle).resolve()
    if bundle.parent.parent.name != "bundles" or not bundle.parent.name.startswith("team-"):
        raise ValueError("현재 지역 모델 지표의 적용 비행 경로가 아닙니다.")
    return confined(bundle.parents[2]/"flight_model_metrics", Path(bundle.parent.name)/(bundle.name+".json"),
                    must_exist=False)


def read_control(bundle, manifest):
    if manifest.get("regional_control") is not None:
        return validate_control(manifest["regional_control"], manifest)
    path = metrics_path(bundle)
    if not path.exists():
        return None
    record = read_json(path)
    expected = pin_fields(Path(bundle), manifest)
    checksum = record.pop("payload_sha256", None)
    if (checksum != canonical_hash(record) or record.get("pin") != expected
            or read_json(Path(bundle)/"READY")["manifest_sha256"] != expected["manifest_sha256"]):
        raise ValueError("보강 제어 집계의 비행 식별자 또는 원본 검사값이 다릅니다.")
    if not record.get("verification", {}).get("original_bundle_unchanged"):
        raise ValueError("원본 비행 보존 검증이 없는 제어 집계입니다.")
    return validate_control(record["control"], manifest)


def store_verified_control(bundle, manifest, row, arrays, scene):
    """Attach retained original observations only after exact existing-channel equality."""
    from .team_recording_parity import verify_replay
    from .team_capture import digest, write_result
    from .team_runtime import file_lock
    bundle = Path(bundle).resolve()
    path = metrics_path(bundle)
    with file_lock(path.with_suffix(".lock"), wait=True):
        original = {name:digest(bundle/name) for name in ("manifest.json", "data.sqlite", "scene.json", "READY")}
        verify_replay(bundle, manifest, row, arrays, scene)
        control = native_control(row)
        validate_control(control, manifest)
        if any(digest(bundle/name) != checksum for name,checksum in original.items()):
            raise ValueError("집계 보강 중 기존 비행이 변경되었습니다.")
        record = dict(pin=pin_fields(bundle, manifest), control=control,
                      verification=dict(original_bundle_unchanged=True, exact_existing_channels=True))
        write_result(path, {**record, "payload_sha256":canonical_hash(record)})
    return str(path)


def regional_metrics(bundle, manifest):
    """A one-region evaluation is never relabelled as the team's released multi-map score."""
    if not manifest["run_id"].startswith("team-") or manifest.get("mode") == "nominal":
        return None
    from .team_evaluation import read_reference
    control = read_control(bundle, manifest)
    forecast = read_reference(bundle, manifest)
    controls = deepcopy(control["summary"]) if control else {}
    forecasts = deepcopy(forecast["summary"]) if forecast else {}
    missing = [f"control.{key}" for key in CONTROL_KEYS if controls.get(key) is None]
    missing += [f"forecast.{key}" for key in FORECAST_KEYS if forecasts.get(key) is None]
    provenance = dict(control=control.get("provenance") if control else None,
                      forecast=forecast.get("metadata") if forecast else None)
    return dict(status="available" if not missing else "partial" if controls or forecasts else "missing",
                identity=pin_fields(Path(bundle), manifest),
                scope=dict(id="selected_region", region=manifest["region"],
                           control="선택한 예측·재배치 적용 비행 1회",
                           forecast="같은 지역의 별도 평가 기준 비행 1회", official_release=False),
                control=controls, forecast=forecasts, provenance=provenance, missing=missing)


def restore_control(output_root, rid, revision, observation_dir, snapshot=None):
    """Verify a previously retained full flight; this command never runs a simulation."""
    import os
    import sys
    import numpy as np
    from result_service.dashboard_contract.validation import validate_bundle
    from .team_runtime import native_path
    output_root = Path(output_root).resolve()
    bundle = confined(output_root/"bundles", Path(rid)/revision, must_exist=False)
    manifest = validate_bundle(bundle)
    if (manifest["run_id"], manifest["bundle_revision"]) != (rid, revision):
        raise ValueError("제어 집계를 연결할 실행 식별자가 다릅니다.")
    snapshot = Path(snapshot).resolve() if snapshot else (
        output_root.parent/"team-update"/"versions"/manifest["team_commit"]/"snapshot.json")
    prepared = read_json(snapshot)
    if prepared["sha"] != manifest["team_commit"]:
        raise ValueError("집계 함수의 팀 코드 버전이 원 비행과 다릅니다.")
    folder = Path(observation_dir).resolve()
    row = read_json(folder/"flight.json")
    scene = Path(read_json(folder/"source.json")["scene"])
    with np.load(folder/"frames.npz", allow_pickle=False) as archive:
        arrays = {name:archive[name] for name in archive.files}
    code, previous = native_path(snapshot.parent/"code"), Path.cwd()
    sys.path.insert(0, str(code))
    try:
        os.chdir(code)
        from run_latest_final_v1 import activate_latest
        activate_latest()
        from fast_halfsec import evaluation
        if not Path(evaluation.__file__).resolve().is_relative_to(code):
            raise ValueError("다른 버전의 제어 집계 코드가 불러와졌습니다.")
        return store_verified_control(bundle, manifest, row, arrays, scene)
    finally:
        os.chdir(previous)
        sys.path.remove(str(code))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Verify retained observations and add original control statistics; no new flight")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pin", nargs=2, required=True)
    parser.add_argument("--observation-dir", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    print(restore_control(args.output_root, *args.pin, args.observation_dir, args.snapshot), flush=True)
