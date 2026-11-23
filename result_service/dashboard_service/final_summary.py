"""Read frozen Final V1 performance evidence without importing research modules."""
import hashlib
import json
import math
from pathlib import Path

BASE = "code/final_v1/artifacts/"
FILES = ("completed.json", "release.json", "comparison.json", "comparison_freeze.json", "protocol.json", "transport.json")
PROFILE = "code/forecast_learning/artifacts/freeze.json"

def _hash(payload):
    return hashlib.sha256(payload).hexdigest()

def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite_final_v1_evidence")
    if isinstance(value, dict):
        for child in value.values(): _finite(child)
    elif isinstance(value, list):
        for child in value: _finite(child)

def final_v1_summary(project):
    root = Path(project).resolve()
    if not (root / BASE).is_dir():
        return None
    refs = []
    try:
        manifest_path = root / "FILE_MANIFEST.json"
        if manifest_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("oversize_delivery_manifest")
        delivered = json.loads(manifest_path.read_text(encoding="utf-8-sig"))["files"]
        def read(relative):
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("unsafe_or_oversize_evidence")
            payload = path.read_bytes()
            expected = delivered.get(relative, {})
            fingerprint = _hash(payload)
            if expected.get("bytes") != len(payload) or expected.get("sha256") != fingerprint:
                raise ValueError("delivery_hash_mismatch:" + relative)
            value = json.loads(payload.decode("utf-8-sig"))
            _finite(value)
            refs.append(dict(path=relative, sha256=fingerprint, bytes=len(payload), hash_kind="sha256_bytes"))
            return value, fingerprint
        docs = {}
        hashes = {}
        for name in FILES:
            docs[name], hashes[name] = read(BASE + name)
        profile, _ = read(PROFILE)
        release, comparison, frozen = (docs[n] for n in ("release.json","comparison.json","comparison_freeze.json"))
        protocol, transport = docs["protocol.json"], docs["transport.json"]
        if docs["completed.json"]["release_sha256"] != hashes["release.json"]:
            raise ValueError("release_completion_mismatch")
        for name in ("comparison.json","comparison_freeze.json","protocol.json","transport.json"):
            bindings = [v for k,v in release["evidence_sha256"].items()
                        if k.replace("\\","/").endswith("/final_v1/artifacts/" + name)]
            if bindings != [hashes[name]]:
                raise ValueError("release_evidence_mismatch:" + name)
        if comparison["freeze_sha256"] != hashes["comparison_freeze.json"]:
            raise ValueError("forecast_freeze_mismatch")
        if frozen["pins"]["protocol_sha256"] != hashes["protocol.json"]:
            raise ValueError("protocol_freeze_mismatch")
        if transport["decision_sha256"] != hashes["comparison.json"]:
            raise ValueError("transport_decision_mismatch")
        selected = release["controller"]
        if selected != "v21" or comparison["candidate"] != selected:
            raise ValueError("unsupported_final_v1_release")
        stats = {k: comparison["stats"][k] for k in ("v7", selected)}
        cases = len(protocol["confirmation"])
        if any(row["flights"] != cases for row in stats.values()):
            raise ValueError("confirmation_count_mismatch")
        return dict(
            status="available", label="Final V1 · 최종 확인 평가", research_version="Final V1",
            provenance_status="delivery_and_release_hash_verified",
            release={k: release.get(k) for k in ("version","controller","transport_fallback","created_at",
                "confirmation_flights","stress_flights","replay_cases","scope","hardware_validated")},
            control=dict(selected=selected, baseline="v7", cases=cases, stats=stats,
                vs_v21=comparison["vs_v21"]["v7"], control_threshold=profile["chosen"]["threshold"],
                threshold_source=PROFILE, scope="동일 조건별 실제 제어 비행 비교"),
            forecast=dict(scope="같은 V7 비행 관측 이력에서 예측 점수만 비교",
                baseline="v20", selected="v21", flights=stats["v7"]["flights"],
                thresholds={k:frozen["thresholds"][k] for k in ("v20","v21")},
                scores={k:comparison["alerts"][k] for k in ("v20","v21")}),
            transport={k:transport[k] for k in ("cases","control","transport","fallback_delta","recovery_delta")},
            source_refs=refs, definitions={
                "persistent_onsets":"수색망 단절이 1초 이상 지속된 새 시작. 실행 시작부터 단절인 구간은 새 시작에서 제외",
                "starts_per_h":"전체 새 지속 단절 시작 수 / 전체 시뮬레이션 시간(h)",
                "cut_s_per_h":"1초 이상 지속 단절 구간의 전체 길이(s) / 전체 시뮬레이션 시간(h)",
                "timely_pdr":"생성 패킷 수로 가중한 원본 기한 내 전달률. 일반 PDR과 구분",
                "mean_relay_m":"실행별 중계기 실제 이동 거리 합의 산술 평균",
                "forecast_window":"사건 시작보다 3초 이상 7초 미만 앞선 경보",
                "forecast_precision":"매칭된 최초 경보 수 / 전체 경보 수",
                "delta":"baseline_minus_selected",
                "ci95":"기록된 공간 그룹별 paired bootstrap 95% 구간",
                "completion":"release.created_at은 출시 기록 생성 시각이며 개별 실행 완료 시각이 아님",
            }, limitations=[
                "기존 평가 집합의 저장된 집계이며 대표 4지도 시연 평균이 아님",
                "제어 성능(V7 대 V21)과 같은 관측 예측 성능(V20 대 V21)은 다른 비교",
                "과거 V5 단절 횟수와 지속 단절의 정의가 다르므로 그대로 합산하지 않음",
                "명령 손실 실험은 별도 14조건이며 기본 42조건과 합산하지 않음",
                "이 화면은 저장된 근거를 표시하며 연구 실행이나 모델 학습을 시작하지 않음",
            ])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return dict(status="unavailable", label="Final V1 · 최종 확인 평가",
                    research_version="Final V1", reason=str(exc), source_refs=refs,
                    provenance_status="verification_failed")
