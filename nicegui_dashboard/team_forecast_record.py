"""Identity and file format of a supplemental forecast for one sealed flight."""
import math
from pathlib import Path
import re

from result_service.dashboard_contract.validation import canonical_hash, read_json


FLEET_FORECAST_HEAD = {
    "window_s": [3, 7], "window_bounds": "[)", "minimum_outage_s": 1,
    "scope": "next_persistent_search_network_outage",
    "source": "final_v1.halfsec_joint.model.JointPolicy.forecast/fleet_logits",
}


def forecast_path(output_root, rid, revision):
    if not all(isinstance(value, str) and re.fullmatch(r"[a-z0-9_-]{8,80}", value) for value in (rid, revision)):
        raise ValueError("비행 또는 변환본 이름이 올바르지 않습니다.")
    target = Path(output_root).resolve() / "flight_forecasts" / rid / (revision + ".json")
    if target.resolve() != target:
        raise ValueError("예측 기록 경로가 선택한 비행과 다릅니다.")
    return target


def forecast_digest(payload):
    return canonical_hash({key: val for key, val in payload.items() if key != "payload_sha256"})


def _probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def load_forecast(path, manifest, manifest_sha256):
    data = read_json(path)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("지원하지 않는 단절 예측 기록입니다.")
    if data.get("payload_sha256") != forecast_digest(data):
        raise ValueError("단절 예측 기록의 검사값이 다릅니다.")
    expected = {key: manifest[key] for key in ("run_id", "bundle_revision", "team_commit", "model_sha256", "map_hash")}
    expected.update(manifest_sha256=manifest_sha256, data_sha256=manifest["artifacts"]["data.sqlite"]["sha256"],
                    head_definition=FLEET_FORECAST_HEAD)
    if any(data.get(key) != value for key, value in expected.items()):
        raise ValueError("단절 예측과 원래 비행 기록의 출처가 다릅니다.")
    threshold = data.get("model_threshold")
    if threshold is not None and (not _probability(threshold) or
            manifest.get("model_threshold", threshold) != threshold):
        raise ValueError("단절 판단 기준이 원래 모델과 다릅니다.")
    count = manifest["validation_report"]["counts"]["decisions"]
    p, before = data.get("probabilities"), data.get("pre_action_connected")
    if (manifest["clocks"]["decision_dt_s"] != .5 or manifest["simulation_end_s"] != count * .5
            or not isinstance(p, list) or not isinstance(before, list) or not count or len(p) != count or len(before) != count
            or any(v is not None and not _probability(v) for v in p)
            or before[0] is not None or any(type(v) is not bool for v in before[1:])):
        raise ValueError("단절 예측의 판단 시각 또는 값이 올바르지 않습니다.")
    return data
