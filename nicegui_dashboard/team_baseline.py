"""Measure the original two-arm comparison with the selected team simulator."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile

from result_service.dashboard_contract.validation import canonical_hash, confined, read_json, validate_bundle
from result_service.dashboard_service.common import database, decode
from .team_baseline_record import comparison_path, load_comparison, nominal_bundle, pin_fields
from .team_bundle import make_scene, totals, validate_capture, write_database, write_nominal_bundle
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


def capture_pair(code, core, applied, arrays, *, include_evaluation=True):
    import numpy as np

    validate_capture(applied, arrays)
    # The default request is also understood by a recorder already in flight
    # when the dashboard is updated. Only backfills need the extra option.
    options = {} if include_evaluation else {"include_evaluation":False}
    nominal, frames, scene = capture(code, core, applied["spec"], applied["team_commit"], nominal=True, **options)
    validate_capture(nominal, frames)
    start, baseline = arrays["positions"][0], frames["positions"][0]
    same_start = np.array_equal(start, baseline)
    placement = applied.get("placement") or {}
    station = placement.get("station")
    if not same_start and applied["controller"] == "halfsec_planned_eight" and station:
        relay = station["relay"]
        same_start = (placement.get("map_sha256") == applied["map_sha256"]
                      and start.shape == baseline.shape and applied["search_drones"] < relay < len(start)
                      and np.array_equal(start[:applied["search_drones"]], baseline[:applied["search_drones"]])
                      and np.allclose(start[relay], station["xy_m"], atol=1e-5, rtol=0.))
    if (nominal["controller"] != "nominal_no_relocation" or applied["controller"] == nominal["controller"]
            or any(applied[k] != nominal[k] for k in COMPARABLE) or not same_start):
        raise ValueError("두 비행의 지도·시작 대형·모델 배포본 또는 실험 조건이 다릅니다.")
    return nominal, frames


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


def verify_restored_nominal(bundle, manifest, arm, nominal, frames, scene):
    """A summary may authorize replay only for the same measured no-command flight."""
    import numpy as np

    fleet = [dict(id=f"uav-{i}", role="search" if i < nominal["search_drones"] else "relay")
             for i in range(nominal["total_drones"])]
    if (nominal["spec"] != manifest["source_identity"]["spec"] or fleet != manifest["fleet"]
            or nominal["team_commit"] != manifest["team_commit"] or nominal["map_sha256"] != manifest["map_hash"]
            or nominal["model_sha256"] != manifest["model_sha256"] or nominal["controller"] != "nominal_no_relocation"
            or any(manifest.get(key) != nominal.get(key) for key in
                   ("decision_interval_s", "split", "map_input_source", "source_study_role") if key in manifest)
            or ("model_threshold" in manifest and nominal.get("model_threshold") != manifest["model_threshold"])
            or nominal["events"] or nominal["applied_changes"] or np.any(frames["applied_offsets"])
            or make_scene(scene, nominal) != read_json(bundle / "scene.json")
            or comparison_arm(manifest, nominal, "nominal") != arm):
        raise ValueError("복원한 기본 대형의 실행 조건·지도·모델·측정값이 저장한 비행과 다릅니다.")
    with database(bundle) as db:
        first = decode(db.execute("SELECT payload FROM positions WHERE time_s=0").fetchone()[0])
    points = {sample["id"]: [sample["x"], sample["y"]] for sample in first["uavs"]}
    # A planned deployment may intentionally start a relay at its assigned station.
    # The legacy controller used one starting formation for both arms.
    shared = nominal["search_drones"] if manifest["controller"] == "halfsec_planned_eight" else len(fleet)
    original = [points[u["id"]] for u in fleet[:shared]]
    if not np.array_equal(frames["positions"][0, :shared], original):
        raise ValueError("기본 대형 경로와 보관된 비행의 시작 위치가 다릅니다.")


def recover_pair(output_root, rid, revision, snapshot=None, *, with_replay=False):
    from .team_forecast_backfill import compare_recordings

    if not re.fullmatch(r"team-[0-9a-f]{28}", rid) or not re.fullmatch(r"rev-[0-9a-f]{24}", revision):
        raise ValueError("선택한 팀 비행의 고정 실행 식별자를 확인하세요.")
    root = Path(output_root).resolve()
    bundle = confined(root / "bundles", Path(rid) / revision, must_exist=False)
    with file_lock(comparison_path(bundle).with_suffix(".lock"), wait=True):
        manifest = validate_bundle(bundle)
        if (manifest["run_id"], manifest["bundle_revision"]) != (rid, revision):
            raise ValueError("선택한 실행과 보관 경로의 식별자가 다릅니다.")
        pair = load_comparison(bundle)
        replay = nominal_bundle(bundle)
        if pair and (not with_replay or (replay / "READY").is_file()):
            if with_replay:
                m = validate_bundle(replay)
                if any(m[k] != pair["nominal"][k] for k in pair["nominal"]):
                    raise ValueError("기존 기본 대형 재생과 동일 비행의 비교 기록이 다릅니다.")
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
        if pair:
            nominal, frames, scene = capture(code, core, manifest["source_identity"]["spec"], sha, nominal=True,
                                             include_evaluation=False)
            verify_restored_nominal(bundle, manifest, pair["nominal"], nominal, frames, scene)
            _publish_recovered(root, replay, bundle, manifest, pair["nominal"], nominal, frames)
            return "recovered"
        applied, arrays, scene = capture(code, core, manifest["source_identity"]["spec"], sha)
        verify_applied(manifest, applied)
        if make_scene(scene, applied) != read_json(bundle / "scene.json"):
            raise ValueError("재측정한 지도와 저장된 지도가 다릅니다.")
        original_row, original_channels = applied, arrays
        if manifest.get("capture_version") != "3":
            from .team_telemetry import legacy_capture_view
            original_row, original_channels = legacy_capture_view(applied, arrays,
                                            "fleet_onset_probability" in manifest["head_definitions"])
        with tempfile.TemporaryDirectory(prefix="team-pair-verification-") as folder:
            path = Path(folder) / "data.sqlite"
            write_database(path, original_row, original_channels, manifest["fleet"], validate_capture(applied, arrays),
                           manifest["metric_definitions"])
            compare_recordings(bundle / "data.sqlite", path)
        nominal, frames = capture_pair(code, core, applied, arrays, include_evaluation=False)
        store_pair(bundle, applied, nominal)
        if with_replay:
            _publish_recovered(root, replay, bundle, manifest,
                               comparison_arm(manifest, nominal, "nominal"), nominal, frames)
        return "recovered"


def _publish_recovered(root, replay, bundle, manifest, arm, nominal, frames):
    from .ingest import load_store, publish
    from .managed_service import SERVICE_PROJECT
    with tempfile.TemporaryDirectory(prefix="team-nominal-recovery-") as staging:
        target = Path(staging)/replay.parent.name/replay.name
        write_nominal_bundle(target, manifest, arm, nominal, frames, (bundle/"scene.json").read_bytes())
        store = load_store(SERVICE_PROJECT, root, quota_gib=float(os.environ.get("UAV_RESULT_MAX_GIB", 4)))
        try:
            publish(target, store)
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--pin", required=True, nargs=2, metavar=("RUN", "REVISION"))
    parser.add_argument("--snapshot", type=Path, help="Prepared snapshot for this job's pinned team revision")
    parser.add_argument("--with-replay", action="store_true", help="Restore and verify the same-version baseline trajectory")
    args = parser.parse_args()
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    print(json.dumps({"pair": recover_pair(args.output_root, *args.pin, snapshot=args.snapshot,
                                           with_replay=args.with_replay)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
