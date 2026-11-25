"""Evaluate the deployed model on the team's independently flown warm-reference trajectory."""
from bisect import bisect_left
from contextlib import ExitStack
from functools import partial
import hashlib
import json
import math
from pathlib import Path
import statistics
import tempfile


ARTIFACT = "regional_evaluation.json"


def evaluation_path(bundle):
    from result_service.dashboard_contract.validation import confined
    bundle = Path(bundle).resolve()
    if bundle.parent.parent.name != "bundles" or not bundle.parent.name.startswith("team-"):
        raise ValueError("선택한 적용 비행의 경로를 확인할 수 없습니다.")
    return confined(bundle.parents[2] / "flight_evaluations",
                    Path(bundle.parent.name) / (bundle.name + ".json"), must_exist=False)


def reference_descriptor(root, manifest):
    if manifest.get("regional_evaluation"):
        return manifest["regional_evaluation"]
    if not manifest["run_id"].startswith("team-"):
        return None
    payload = read_reference(root, manifest)
    if payload:
        return dict(region=manifest["region"], summary=payload["summary"],
                    chunk=f"team-evaluation/{manifest['run_id']}/{manifest['bundle_revision']}")
    return None


def event_rows(cuts, times, scores, threshold, duration, summary):
    """Identify which event each official rising-edge alert matched for the replay table."""
    events = [
        dict(id=i, start_s=float(start), end_s=float(end) if end < duration else None,
             warning_time_s=None, lead_s=None, hit=False)
        for i, (start, end) in enumerate(((a, b) for a, b in cuts if a > 0), 1)
    ]
    onsets = [event["start_s"] for event in events]
    alerts, leads = [], []
    previous = False
    for when, score in zip(times, scores, strict=True):
        above = score is not None and score >= threshold
        if above and not previous:
            alerts.append(dict(time_s=when))
            for i in range(bisect_left(onsets, when + 3), bisect_left(onsets, when + 7)):
                if not events[i]["hit"]:
                    lead = onsets[i] - when
                    events[i].update(hit=True, warning_time_s=when, lead_s=lead)
                    leads.append(lead)
                    break
        previous = above
    counts = (len(events), len(alerts), len(leads), statistics.median(leads) if leads else None)
    if counts != tuple(summary[k] for k in ("events", "alerts", "hits", "median_lead_s")):
        raise ValueError("사건별 목록과 팀 평가 함수의 경보·탐지 집계가 일치하지 않습니다.")
    return events, alerts


def evaluate_reference(campaign, row, arrays, threshold, metadata):
    import numpy as np

    with tempfile.TemporaryDirectory(prefix="uav-team-evaluation-") as temp:
        path = Path(temp) / "reference.npz"
        np.savez(path, **{key: arrays[key] for key in ("network", "plan_frames", "scores", "comparison_scores")})
        case, = campaign.forecast_cases([dict(row, archive_path=str(path))])
    # The release report's operational (not recalibrated) operating point.
    summary = campaign.evaluate([case], "joint", threshold)
    times = case["times"].tolist()
    scores = [float(s) if math.isfinite(s) else None for s in case["scores"]["joint"]]
    events, alerts = event_rows(row["cuts"], times, scores, threshold, row["duration_s"], summary)
    return dict(region=row["spec"]["region"], metadata={**metadata, "duration_s":row["duration_s"],
                "spec":row["spec"], "threshold":threshold, "window":[3, 7]},
                summary=summary, events=events, alerts=alerts,
                series=dict(times=times, scores=scores, valid=[s is not None for s in scores]))


def capture_reference(entry, release, context, core, card, nominal, scene):
    from final_v1.halfsec_joint import run as campaign
    from .team_demo import demo_survey
    from .team_entry import planned_map

    spec = nominal["spec"]
    initial = campaign.pointer(0)
    warm, operating_threshold = campaign.worker_model(**initial)
    frozen = json.loads(campaign.FREEZE.read_text(encoding="utf-8-sig"))
    if (frozen["protocol"] != campaign.sha(campaign.PLAN) or frozen["initial"]["step"] != 0
            or frozen["initial"]["sha256"] != initial["sha256"]
            or frozen["operational_threshold"] != operating_threshold):
        raise ValueError("원래 평가의 기준 모델·프로토콜 또는 고정 경보 기준을 검증할 수 없습니다.")
    deployed, deployed_threshold = release.load_model()
    deployed_record = json.loads(release.MANIFEST.read_text(encoding="utf-8-sig"))
    if deployed_record["sha256"] != nominal["model_sha256"]:
        raise ValueError("별도 기준 비행에 적용할 모델과 선택한 비행의 모델이 다릅니다.")
    with ExitStack() as scope:
        if entry is not release:
            scope.enter_context(planned_map(entry, context, spec, core, card, nominal=True))
            scope.enter_context(entry.map_registry(spec["region"]))
            survey = entry.planned_eight.scene_for
        else:
            scope.enter_context(release.map_scope(spec["region"]))
            survey = (partial(demo_survey, context, card, scene)
                      if nominal["map_input_source"] == "dashboard_demonstration" else context.survey_for)
        flight = campaign.JointFlight(warm, operating_threshold, explore=False, comparison=deployed)
        surveyed_deployments = set()
        def verified_survey(region, direction):
            data = survey(region, direction)
            city, _, fleet, _, _ = data
            if (city.sha256, fleet.n, fleet.ns) != (
                    nominal["map_sha256"], nominal["total_drones"], nominal["search_drones"]):
                raise ValueError("평가 기준 비행의 지도 또는 편대가 선택한 지역 조건과 다릅니다.")
            surveyed_deployments.add(fleet.key)
            return data
        flight.survey_factory = verified_survey
        row, arrays = flight.run(spec, .5, nominal["full_mission_budget_s"])
    if (row["spec"] != spec or surveyed_deployments != {row["deployment"]}
            or (nominal.get("deployment") is not None and row["deployment"] != nominal["deployment"])
            or not row["safety_passed"] or not row["packet_conserved"]):
        raise ValueError("지역 예측 평가 기준 비행의 편대·안전·패킷 검증을 통과하지 못했습니다.")
    metadata = dict(reference_controller="warm_05s", evaluated_model="joint",
                    evaluated_step=deployed_record["step"], report_step=frozen["selected"]["step"],
                    protocol_sha256=frozen["protocol"], report_checkpoint_sha256=frozen["selected"]["sha256"],
                    reference_checkpoint_sha256=initial["sha256"], model_sha256=nominal["model_sha256"],
                    deployed_threshold=deployed_threshold, team_commit=nominal["team_commit"],
                    map_hash=nominal["map_sha256"], source_split=nominal["split"], deployment=row["deployment"])
    return evaluate_reference(campaign, row, arrays, operating_threshold, metadata)


def check_identity(payload, manifest):
    meta = payload["metadata"]
    if (payload["region"] != manifest["region"] or meta["spec"] != manifest["source_identity"]["spec"]
            or any(meta[k] != manifest[k] for k in ("team_commit", "map_hash", "model_sha256"))):
        raise ValueError("지역 평가의 코드·모델·시나리오가 선택한 비행과 다릅니다.")


def store_reference(bundle, manifest, nominal):
    payload = nominal["_regional_evaluation"]
    check_identity(payload, manifest)
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    (bundle / ARTIFACT).write_bytes(data)
    manifest["artifacts"][ARTIFACT] = dict(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    rid, revision = manifest["run_id"], manifest["bundle_revision"]
    manifest["regional_evaluation"] = dict(region=manifest["region"], summary=payload["summary"],
                                           chunk=f"team-evaluation/{rid}/{revision}")


def read_reference(root, manifest):
    descriptor = manifest.get("regional_evaluation")
    if not descriptor:
        root = Path(root)
        if root.parent.parent.name != "bundles" or not root.parent.name.startswith("team-"):
            return None
        from result_service.dashboard_contract.validation import canonical_hash, read_json
        from .team_baseline_record import pin_fields
        path = evaluation_path(root)
        if not path.exists():
            return None
        record = read_json(path)
        digest = record.pop("payload_sha256", None)
        pin = pin_fields(root, manifest)
        if (digest != canonical_hash(record) or record.get("pin") != pin
                or read_json(root/"READY", 4096)["manifest_sha256"] != pin["manifest_sha256"]):
            raise ValueError("선택한 비행과 지역 평가의 식별자 또는 검사값이 다릅니다.")
        payload = record["evaluation"]
        check_identity(payload, manifest)
        return payload
    artifact = manifest["artifacts"][ARTIFACT]
    data = (Path(root) / ARTIFACT).read_bytes()
    if len(data) != artifact["size_bytes"] or hashlib.sha256(data).hexdigest() != artifact["sha256"]:
        raise ValueError("선택한 비행의 지역 평가 파일이 검사값과 다릅니다.")
    payload = json.loads(data)
    check_identity(payload, manifest)
    if (descriptor["region"] != payload["region"] or descriptor["summary"] != payload["summary"]
            or descriptor["chunk"] != f"team-evaluation/{manifest['run_id']}/{manifest['bundle_revision']}"):
        raise ValueError("지역 평가 집계와 선택한 비행 식별자가 일치하지 않습니다.")
    return payload
