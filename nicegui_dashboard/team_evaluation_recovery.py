"""Run only the official reference evaluation for an existing, immutable applied flight."""
import argparse
import os
from pathlib import Path
import sys

from result_service.dashboard_contract.validation import canonical_hash, confined, read_json, validate_bundle
from .team_baseline_record import pin_fields
from .team_evaluation import (capture_reference, check_identity, evaluation_path, read_reference,
                              verify_reference_upgrade)
from .team_runtime import file_lock, native_path


def measure(manifest, snapshot, *, reuse_scores=None):
    from .team_demo import resolve_demo_scene
    from .team_entry import selected_entry

    prepared = read_json(snapshot)
    sha = manifest["team_commit"]
    if prepared["sha"] != sha:
        raise ValueError("지역 평가에 사용할 코드와 비행의 팀 버전이 다릅니다.")
    code = native_path(snapshot.parent / "code")
    core = Path(prepared["release_root"]).resolve()
    spec = manifest["source_identity"]["spec"]
    if any(spec.get(k) != manifest[k] for k in ("region", "seed", "kind", "direction")):
        raise ValueError("비행 지역 또는 시나리오와 원본 실행 조건이 다릅니다.")
    cards = [c for c in read_json(core/"dashboard_execution/selected_maps.json")["maps"]
             if c["key"] == spec["region"]]
    if len(cards) != 1 or cards[0]["sha256"] != manifest["map_hash"]:
        raise ValueError("평가 지도의 원본과 선택한 비행의 지도가 다릅니다.")
    card = cards[0]
    previous = Path.cwd()
    sys.path.insert(0, str(code))
    try:
        os.chdir(code)
        from run_latest_final_v1 import activate_latest
        activate_latest()
        from fast_halfsec import context
        from final_v1 import fly
        from final_v1.halfsec_main import DECISION_S, MODE, release
        from swarm_sim.mission import MAX_MISSION
        entry, _, controller = selected_entry(fly, release, MODE)
        if (not Path(release.__file__).resolve().is_relative_to(code)
                or controller != manifest["controller"] or DECISION_S != manifest["decision_interval_s"]):
            raise ValueError("평가에 쓰는 제어기 또는 판단 주기가 원래 비행과 다릅니다.")
        conditions = dict(spec=spec, team_commit=sha, map_sha256=manifest["map_hash"],
                          model_sha256=manifest["model_sha256"], split=manifest["split"],
                          map_input_source=manifest["map_input_source"], total_drones=len(manifest["fleet"]),
                          search_drones=sum(u["role"] == "search" for u in manifest["fleet"]),
                          full_mission_budget_s=MAX_MISSION)
        scene = resolve_demo_scene(core, card) if conditions["map_input_source"] == "dashboard_demonstration" else None
        options = {"reuse_scores":reuse_scores} if reuse_scores is not None else {}
        return capture_reference(entry, release, context, core, card, conditions, scene, **options)
    finally:
        os.chdir(previous)
        sys.path.remove(str(code))


def recover(output, rid, revision, snapshot, *, with_ap=False, reuse_sealed_scores=False):
    from .team_capture import write_result

    bundle = confined(Path(output).resolve()/"bundles", Path(rid)/revision, must_exist=False)
    if not bundle.is_dir() or (bundle.parent.name, bundle.name) != (rid, revision):
        raise ValueError("잘못된 지역 평가 실행 식별자입니다.")
    target = evaluation_path(bundle)
    with file_lock(target.with_suffix(".lock"), wait=True):
        manifest = validate_bundle(bundle)
        if ((manifest["run_id"], manifest["bundle_revision"]) != (rid, revision)
                or manifest.get("adapter_id") != "team-halfsec" or manifest["mode"] == "nominal"):
            raise ValueError("지역 평가는 선택한 팀 적용 비행에서만 실행합니다.")
        previous = read_reference(bundle, manifest)
        if previous is not None and (not with_ap or "average_precision" in previous["summary"]):
            return "already-recorded"
        options = ({"reuse_scores":dict(evaluation=previous,pin=pin_fields(bundle,manifest))}
                   if reuse_sealed_scores and previous is not None else {})
        evaluation = measure(manifest, Path(snapshot).resolve(), **options)
        check_identity(evaluation, manifest)
        if previous is not None:
            verify_reference_upgrade(previous, evaluation)
        record = dict(pin=pin_fields(bundle, manifest), evaluation=evaluation)
        write_result(target, {**record, "payload_sha256":canonical_hash(record)})
        return "recovered"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--pin", nargs=2, required=True)
    parser.add_argument("--with-ap", action="store_true")
    parser.add_argument("--reuse-sealed-scores", action="store_true",
                        help="Audited e7 backfills only: preserve the warm flight and reuse its sealed comparison scores")
    args = parser.parse_args()
    print(recover(args.output_root, *args.pin, args.snapshot, with_ap=args.with_ap,
                  reuse_sealed_scores=args.reuse_sealed_scores), flush=True)
