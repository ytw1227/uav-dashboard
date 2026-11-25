"""Observe one unchanged regional replay and attach operation details only after exact parity."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile

from result_service.dashboard_contract.validation import read_json, validate_bundle
from .team_bundle import digest, make_scene, totals, validate_capture, write_database
from .team_capture import capture, write_result
from .team_forecast_backfill import compare_recordings
from .team_operations import load_operations, operations_path
from .team_operations_payload import make_payload
from .team_runtime import file_lock, native_path


# Re-export for callers of the original observer module.
from .team_operations_probe import progress_sample


def observed_capture(code, core, spec, commit):
    # Normal captures now collect these observations as well. Calling that single
    # path avoids duplicate wrappers or a second applied-policy simulation.
    row, arrays, scene = capture(code, core, spec, commit, include_evaluation=False)
    recording = row.get("_operations_recording")
    if not recording or recording.get("source") != "native_navigation_callbacks":
        raise ValueError("실제 적용 비행의 운용 관측 기록이 없습니다.")
    return row, arrays, scene, recording["progress"], recording["checks"]


from .team_recording_parity import verify_replay


def capture_operations(output_root, rid, revision, *, retain=None, observation_dir=None):
    root = Path(output_root).resolve()
    target = operations_path(root, rid, revision)
    bundle = root / "bundles" / rid / revision
    with file_lock(target.with_suffix(".lock"), wait=True) as locked:
        if not locked:
            raise ValueError("같은 비행의 운용 기록 확인이 이미 진행 중입니다.")
        manifest = validate_bundle(bundle)
        if (manifest["controller"] != "halfsec_planned_eight"
                or manifest["mode"] == "nominal" or manifest.get("capture_version") != "3"):
            raise ValueError("관측 작업은 계획 배치 제어기의 예측·재배치 적용 비행에서 제공합니다.")
        from .team_model_metrics import read_control, restore_control, store_verified_control
        existing = load_operations(root, manifest)
        if existing["status"] == "available" and read_control(bundle, manifest) is not None:
            return dict(status="already_recorded", path=str(target))
        original = {name: digest(bundle / name) for name in ("manifest.json", "data.sqlite", "scene.json", "READY")}
        sha = manifest["team_commit"]
        snapshot = root.parent / "team-update" / "versions" / sha / "snapshot.json"
        core = Path(read_json(snapshot)["release_root"]).resolve()
        print(json.dumps(dict(stage="operation_observation_start", run_id=rid, revision=revision,
                              team_commit=sha, spec=manifest["source_identity"]["spec"])), flush=True)
        if observation_dir is None:
            row, arrays, scene, samples, checks = observed_capture(native_path(snapshot.parent / "code"), core,
                                                            manifest["source_identity"]["spec"], sha)
        else:
            # Restart publication after an I/O interruption without re-running the flight.
            # The same complete parity check below is mandatory for retained observations.
            import numpy as np
            folder = Path(observation_dir).resolve()
            row, samples, checks = (read_json(folder / name) for name in
                                    ("flight.json", "progress.json", "checks.json"))
            scene = Path(read_json(folder / "source.json")["scene"])
            with np.load(folder / "frames.npz", allow_pickle=False) as archive:
                arrays = {name: archive[name] for name in archive.files}
        if retain is not None:
            import numpy as np
            retain = Path(retain).resolve()
            retain.mkdir(parents=True, exist_ok=True)
            write_result(retain / "flight.json", row)
            write_result(retain / "progress.json", samples)
            write_result(retain / "checks.json", checks)
            write_result(retain / "source.json", dict(scene=str(scene)))
            np.savez_compressed(retain / "frames.npz", **arrays)
        verify_replay(bundle, manifest, row, arrays, scene)
        if any(digest(bundle / name) != value for name, value in original.items()):
            raise ValueError("확인 도중 원 비행 파일이 변경되어 운용 기록을 연결하지 않았습니다.")
        payload = make_payload(root, manifest, row, arrays, samples, checks)
        control_path = (restore_control(root, rid, revision, observation_dir, snapshot)
                        if observation_dir is not None else store_verified_control(bundle, manifest, row, arrays, scene))
        if existing["status"] != "available":
            write_result(target, payload)
        load_operations(root, manifest)
        return dict(status="recorded", path=str(target), control_path=control_path, samples=len(samples), windows=len(payload["windows"]),
                    moves=len(payload["moves"]), verification=payload["verification"])


def main():
    import os
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pin", nargs=2, metavar=("RUN", "REVISION"), required=True)
    parser.add_argument("--retain", type=Path)
    parser.add_argument("--reuse-observation", type=Path)
    args = parser.parse_args()
    print(json.dumps(capture_operations(args.output_root, *args.pin, retain=args.retain,
                                       observation_dir=args.reuse_observation), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
