"""Measure the original two-arm comparison with the selected team simulator."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile

from result_service.dashboard_contract.validation import canonical_hash, confined, read_json, validate_bundle
from .team_baseline_record import comparison_path, load_comparison, pin_fields
from .team_bundle import make_scene, totals, validate_capture, write_database
from .team_capture import capture, write_result
from .team_runtime import file_lock, native_path


class NominalFormation:
    """Remove relocation decisions only. The native navigator, clearance logic,
    RF, beacon airtime, routed packets, clock and mission termination still run.
    In fast_halfsec.runner the read-only state/predict output is consumed only
    by candidate selection. There are no candidates in a no-command flight.
    """
    def state_at(self, *args):
        return (None,)

    def predict(self, raw):
        return 0., 0.

    def options_for(self, *args):
        return [], 0


COMPARABLE = ("spec", "team_commit", "model_sha256", "map_sha256", "deployment", "altitude_m",
              "total_drones", "search_drones", "decision_interval_s", "full_mission_budget_s",
              "map_input_source", "source_study_role", "split", "target_cells")


def capture_pair(code, core, applied, arrays):
    import numpy as np

    validate_capture(applied, arrays)
    nominal, frames, scene = capture(code, core, applied["spec"], applied["team_commit"], nominal=True)
    validate_capture(nominal, frames)
    if (nominal["controller"] != "nominal_no_relocation" or applied["controller"] == nominal["controller"]
            or any(applied[k] != nominal[k] for k in COMPARABLE)
            or not np.array_equal(arrays["positions"][0], frames["positions"][0])):
        raise ValueError("두 비행의 지도·시작 대형·모델 배포본 또는 실험 조건이 다릅니다.")
    return nominal


def comparison_arm(manifest, row, mode):
    shared, targets = row["shared_cells"], row["target_cells"]
    if not (type(shared) is int and type(targets) is int and 0 <= shared <= targets and targets > 0):
        raise ValueError("공통 공유 목표 셀 수 또는 전체 목표 셀 수를 확인할 수 없습니다.")
    definitions = dict(manifest["metric_definitions"])
    definitions["search_connected_mean"] = dict(definitions["ctr"], key="search_connected_mean")
    share = dict(key="information_shared_fraction", label="공통 공유율", unit="ratio", scope="run_total",
                 aggregation="intersection of all search known cells after packet drain / all outdoor target cells",
                 source="cooperative.packets.PacketSim.known; adaptive_joint_expanded.world")
    definitions[share["key"]] = dict(share, definition_hash=canonical_hash(share))
    metrics = totals(row)
    metrics.update(search_connected_mean=metrics["ctr"], information_shared_fraction=shared/targets)
    result = {k: manifest[k] for k in ("run_id", "bundle_revision", "region", "seed", "kind", "direction",
                                       "map_hash", "team_commit", "model_sha256", "clocks", "fleet")}
    result.update(pair_key=f'{manifest["run_id"]}/{manifest["bundle_revision"]}', mode=mode,
                  simulation_end_s=row["duration_s"], mission_completed=row["mission_completed"],
                  controller=row["controller"], model_id=row["controller"], final_metrics=metrics,
                  metric_definitions=definitions, raw_metadata=dict(persistent_cut_intervals=row["cuts"],
                      shared_cells=shared, target_cells=targets))
    if mode == "nominal":
        result["run_id"] = "nominal-" + manifest["run_id"].removeprefix("team-")
    return result


def verify_applied(manifest, row):
    expected = dict(team_commit=row["team_commit"], model_sha256=row["model_sha256"], map_hash=row["map_sha256"],
                    controller=row["controller"], decision_interval_s=row["decision_interval_s"],
                    mission_completed=row["mission_completed"], simulation_end_s=row["duration_s"],
                    final_metrics=totals(row))
    if (manifest["adapter_id"] != "team-halfsec" or manifest["source_identity"]["spec"] != row["spec"]
            or any(manifest.get(key) != value for key, value in expected.items())):
        raise ValueError("재측정한 팀 비행과 저장된 비행의 버전·조건·지표가 다릅니다.")


def paired_manifests(manifest, applied, nominal):
    verify_applied(manifest, applied)
    if nominal["controller"] != "nominal_no_relocation" or any(applied[k] != nominal[k] for k in COMPARABLE):
        raise ValueError("다른 실험 또는 재배치 적용 모델을 기본 대형으로 사용할 수 없습니다.")
    return {"nominal": comparison_arm(manifest, nominal, "nominal"), "applied": comparison_arm(manifest, applied, "v21")}


def store_pair(bundle, applied, nominal):
    manifest = read_json(bundle / "manifest.json")
    data = {**pin_fields(bundle, manifest), **paired_manifests(manifest, applied, nominal)}
    write_result(comparison_path(bundle), {**data, "payload_sha256": canonical_hash(data)})


def recover_pair(output_root, rid, revision, snapshot=None):
    from .team_forecast_backfill import compare_recordings

    if not re.fullmatch(r"team-[0-9a-f]{28}", rid) or not re.fullmatch(r"rev-[0-9a-f]{24}", revision):
        raise ValueError("선택한 팀 비행의 고정 실행 식별자를 확인하세요.")
    root = Path(output_root).resolve()
    bundle = confined(root / "bundles", Path(rid) / revision, must_exist=False)
    with file_lock(comparison_path(bundle).with_suffix(".lock"), wait=True):
        manifest = validate_bundle(bundle)
        if load_comparison(bundle):
            return "already-recorded"
        sha = manifest["team_commit"]
        if not re.fullmatch("[0-9a-f]{40}", sha):
            raise ValueError("실행 당시의 팀 코드 버전이 없습니다.")
        snapshot = Path(snapshot).resolve() if snapshot else confined(
            root.parent / "team-update/versions", Path(sha) / "snapshot.json")
        prepared = read_json(snapshot)
        if prepared.get("sha") != sha:
            raise ValueError("비교에 사용할 팀 코드와 선택한 비행의 버전이 다릅니다.")
        core = Path(prepared["release_root"]).resolve()
        code = native_path(snapshot.parent / "code")
        applied, arrays, scene = capture(code, core, manifest["source_identity"]["spec"], sha)
        verify_applied(manifest, applied)
        if make_scene(scene, applied) != read_json(bundle / "scene.json"):
            raise ValueError("재측정한 지도와 저장된 지도가 다릅니다.")
        if "fleet_onset_probability" not in manifest["head_definitions"]:
            original_channels = {k: v for k,v in arrays.items() if k != "fleet_onset_probability"}
        else:
            original_channels = arrays
        with tempfile.TemporaryDirectory(prefix="team-pair-verification-") as folder:
            path = Path(folder) / "data.sqlite"
            write_database(path, applied, original_channels, manifest["fleet"], validate_capture(applied, arrays),
                           manifest["metric_definitions"])
            compare_recordings(bundle / "data.sqlite", path)
        nominal = capture_pair(code, core, applied, arrays)
        store_pair(bundle, applied, nominal)
        return "recovered"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--pin", required=True, nargs=2, metavar=("RUN", "REVISION"))
    parser.add_argument("--snapshot", type=Path, help="Prepared snapshot for this job's pinned team revision")
    args = parser.parse_args()
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    print(json.dumps({"pair": recover_pair(args.output_root, *args.pin, snapshot=args.snapshot)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
