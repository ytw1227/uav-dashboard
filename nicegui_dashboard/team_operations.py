"""Pinned, supplemental operation records; never substitute or modify a flight bundle."""
from copy import deepcopy
from pathlib import Path
import math
import re

from result_service.dashboard_contract.validation import canonical_hash, confined, read_json, sha256


STATES = {"search", "climb", "descent", "recovery", "entry_wait", "descent_check",
          "crossing", "clearing", "complete"}
DUTIES = {"search", "mobile_relay", "fixed_relay", "altitude_relay"}


def operations_path(output_root, rid, revision):
    if not all(isinstance(v, str) and re.fullmatch(r"[a-z0-9_-]{8,80}", v) for v in (rid, revision)):
        raise ValueError("운용 기록의 비행 식별자가 올바르지 않습니다.")
    target = Path(output_root).resolve() / "flight_operations" / rid / (revision + ".json")
    if target.resolve() != target:
        raise ValueError("운용 기록 경로가 선택한 비행과 다릅니다.")
    return target


def operations_digest(payload):
    return canonical_hash({k: v for k, v in payload.items() if k != "payload_sha256"})


def identity_for(output_root, manifest):
    rid, revision = manifest["run_id"], manifest["bundle_revision"]
    operations_path(output_root, rid, revision)
    bundle = Path(output_root).resolve() / "bundles" / rid / revision
    return dict(run_id=rid, revision=revision,
                **{k: manifest[k] for k in ("team_commit", "map_hash", "model_sha256")},
                manifest_sha256=sha256(bundle / "manifest.json"),
                data_sha256=manifest["artifacts"]["data.sqlite"]["sha256"])


def embedded_identity(manifest):
    # The enclosing manifest hashes the operation artifact. Do not put the
    # manifest hash inside that artifact: doing so would create a hash cycle.
    return dict(run_id=manifest["run_id"], revision=manifest["bundle_revision"],
                **{k: manifest[k] for k in ("team_commit", "map_hash", "model_sha256")},
                data_sha256=manifest["artifacts"]["data.sqlite"]["sha256"])


def recording_fingerprint(row):
    return canonical_hash({key: row[key] for key in (
        "_operations_recording", "placement", "altitude_windows", "altitude_moves",
        "coverage", "waypoints_reached", "waypoints_total", "duration_s",
        "cleared_stages", "clearance_edges", "mission_completed")})


def store_operations(bundle, manifest, row, arrays):
    recording = row.get("_operations_recording")
    if recording is None:
        return
    if (recording.get("schema_version") != 1 or recording.get("source") != "native_navigation_callbacks"
            or manifest.get("controller") != "halfsec_planned_eight" or manifest.get("mode") == "nominal"):
        raise ValueError("원 비행에서 수집한 운용 기록의 형식·제어기를 확인할 수 없습니다.")
    payload = make_payload(None, manifest, row, arrays, recording["progress"], recording["checks"], embedded=True)
    from .team_capture import write_result
    path = Path(bundle) / "operations.json"
    write_result(path, payload)
    manifest["artifacts"][path.name] = dict(sha256=sha256(path), size_bytes=path.stat().st_size)
    manifest["operations"] = dict(artifact=path.name, schema_version=1, source="native_navigation_callbacks")


def validate_operations(data, manifest, identity):
    if (not isinstance(data, dict) or data.get("schema_version") != 1
            or data.get("status") != "available" or data.get("identity") != identity
            or data.get("payload_sha256") != operations_digest(data)):
        raise ValueError("운용 기록과 선택한 비행의 버전·지도·모델 또는 검사값이 다릅니다.")
    fleet = manifest["fleet"]
    ids = [item["id"] for item in fleet]
    roles = data.get("roles", [])
    if ([item.get("drone_id") for item in roles] != ids
            or any(item.get("role") != ref["role"] or item.get("duty") not in DUTIES
                   for item, ref in zip(roles, fleet))):
        raise ValueError("운용 역할과 실제 편대 구성이 다릅니다.")
    duration = manifest["simulation_end_s"]
    samples = data.get("progress", [])
    count = round(duration * 2) + 1
    if len(samples) != count:
        raise ValueError("수색 진행 기록의 시각 범위가 원 비행과 다릅니다.")
    total = data.get("summary", {}).get("waypoints_total")
    if type(total) is not int or total < 1 or data["summary"].get("duration_s") != duration:
        raise ValueError("수색 진행 기록의 임무 범위가 올바르지 않습니다.")
    previous = -1
    previous_coverage = -1.
    for i, sample in enumerate(samples):
        reached = sample.get("waypoints_reached")
        heights = sample.get("heights_m", [])
        coverage = sample.get("coverage")
        if (sample.get("time_s") != i * .5 or sample.get("state") not in STATES
                or type(reached) is not int or not previous <= reached <= total
                or sample.get("stage") != reached or sample.get("waypoints_total") != total
                or type(coverage) not in (int, float) or not previous_coverage <= coverage <= 1.
                or len(heights) != len(fleet) or any(type(v) not in (int, float) or not math.isfinite(v) for v in heights)
                or any(type(sample.get(key)) is not bool for key in ("clearing", "entry_waiting", "descent_check"))):
            raise ValueError("수색 진행·고도 기록의 시각 또는 상태가 올바르지 않습니다.")
        previous = reached
        previous_coverage = coverage
    if (samples[-1]["waypoints_reached"] != data["summary"].get("waypoints_reached")
            or samples[-1]["coverage"] != data["summary"].get("coverage")):
        raise ValueError("수색 진행의 마지막 시각과 최종 결과가 다릅니다.")
    for window in data.get("windows", []):
        if window.get("drone_id") not in ids or not isinstance(window.get("id"), str):
            raise ValueError("고도 구간의 담당 기체가 올바르지 않습니다.")
        for key in ("started_s", "ready_s", "entry_request_s", "verified_s", "finished_s"):
            t = window.get(key)
            if t is not None and (type(t) not in (int, float) or not 0 <= t <= duration):
                raise ValueError("고도 구간의 기록 시각이 비행 범위를 벗어났습니다.")
    for move in data.get("moves", []):
        if (move.get("drone_id") not in ids or move.get("kind") not in {"climb", "descent", "recovery"}
                or not 0 <= move["request_s"] <= move["start_s"] <= move["end_s"]):
            raise ValueError("고도 명령의 기체·종류·시각이 올바르지 않습니다.")
    previous_check = -1.
    for check in data.get("checks", []):
        if (check.get("kind") not in {"confirmation", "descent_safe"}
                or check.get("drone_id") not in ids or type(check.get("passed")) is not bool
                or type(check.get("time_s")) not in (int, float)
                or not previous_check <= check["time_s"] <= duration):
            raise ValueError("고도 운용 확인 기록의 시각·기체·반환값이 올바르지 않습니다.")
        previous_check = check["time_s"]
    if not data.get("verification", {}).get("original_bundle_unchanged"):
        raise ValueError("원 비행 보존 검증이 없는 운용 기록입니다.")
    return data


from .team_operations_payload import make_payload


def load_operations(output_root, manifest):
    """Return an explicit missing state; a different flight can never supply this record."""
    identity = {"run_id": manifest["run_id"], "revision": manifest["bundle_revision"],
                **{k: manifest.get(k) for k in ("team_commit", "map_hash", "model_sha256")}}
    if (manifest.get("controller") != "halfsec_planned_eight"
            or manifest.get("mode") == "nominal"):
        return dict(schema_version=1, status="unsupported", identity=identity,
                    message="운용 과정 보기는 계획 배치 제어기의 예측·재배치 적용 비행에서 제공합니다.")
    target = operations_path(output_root, manifest["run_id"], manifest["bundle_revision"])
    descriptor = manifest.get("operations")
    if descriptor is not None:
        if descriptor != dict(artifact="operations.json", schema_version=1, source="native_navigation_callbacks"):
            raise ValueError("저장된 운용 기록의 형식을 확인할 수 없습니다.")
        bundle = Path(output_root).resolve() / "bundles" / manifest["run_id"] / manifest["bundle_revision"]
        path = confined(bundle, descriptor["artifact"])
        artifact = manifest["artifacts"].get(path.name)
        if (not artifact or path.stat().st_size != artifact.get("size_bytes")
                or sha256(path) != artifact.get("sha256")):
            raise ValueError("원 비행의 운용 기록 검사값이 다릅니다.")
        return validate_operations(read_json(path), manifest, embedded_identity(manifest))
    if not target.is_file():
        return dict(schema_version=1, status="not_recorded", identity=identity,
                    message="이 비행의 운용 단계 기록이 아직 연결되지 않았습니다.")
    return validate_operations(read_json(target), manifest, identity_for(output_root, manifest))
