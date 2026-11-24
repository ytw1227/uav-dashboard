"""Read a same-release comparison attached to one immutable flight."""
from pathlib import Path

from result_service.dashboard_contract.validation import canonical_hash, confined, read_json, sha256


def comparison_path(bundle):
    bundle = Path(bundle).resolve()
    if bundle.parent.parent.name != "bundles":
        raise ValueError("지역 실행 기록의 경로를 확인할 수 없습니다.")
    base = bundle.parent.parent.parent / "flight_comparisons"
    return confined(base, Path(bundle.parent.name) / (bundle.name + ".json"), must_exist=False)


def pin_fields(bundle, manifest):
    return dict(schema_version=1, run_id=manifest["run_id"], bundle_revision=manifest["bundle_revision"],
                team_commit=manifest["team_commit"], map_hash=manifest["map_hash"],
                model_sha256=manifest["model_sha256"], manifest_sha256=sha256(bundle / "manifest.json"),
                data_sha256=manifest["artifacts"]["data.sqlite"]["sha256"])


def load_comparison(bundle):
    """Return the two display manifests, or None when no pair has been measured."""
    path = comparison_path(bundle)
    manifest = read_json(bundle / "manifest.json")
    expected = pin_fields(bundle, manifest)
    if read_json(bundle / "READY")["manifest_sha256"] != expected["manifest_sha256"]:
        raise ValueError("완료 표시의 비행과 비교에서 선택한 비행의 검사값이 다릅니다.")
    if "flight_comparison" in manifest:
        data = manifest["flight_comparison"]
    elif path.is_file():
        data = read_json(path)
        signature = data.pop("payload_sha256", None)
        if canonical_hash(data) != signature or any(data.get(k) != v for k, v in expected.items()):
            raise ValueError("실험 비교 결과의 비행 버전 또는 검사값이 다릅니다.")
    else:
        return None
    common = {k: manifest[k] for k in ("bundle_revision", "region", "seed", "kind", "direction", "map_hash",
                                       "team_commit", "model_sha256", "clocks", "fleet")}
    rid = manifest["run_id"]
    for name, mode, arm_id in (("nominal", "nominal", "nominal-" + rid.removeprefix("team-")), ("applied", "v21", rid)):
        arm = data.get(name)
        expected_arm = dict(common, mode=mode, run_id=arm_id, pair_key=f'{rid}/{manifest["bundle_revision"]}')
        if not isinstance(arm, dict) or any(arm.get(k) != v for k, v in expected_arm.items()):
            raise ValueError("두 비행의 지도·모델·대형·시각 기준 또는 고정 실행 식별자가 다릅니다.")
    if (data["applied"].get("controller") != manifest["controller"]
            or data["nominal"].get("controller") != "nominal_no_relocation"):
        raise ValueError("실험 비교 결과의 비행 버전 또는 검사값이 다릅니다.")
    return {"nominal": data["nominal"], "applied": data["applied"]}
