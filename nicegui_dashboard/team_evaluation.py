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
SCORE_REUSE_COMMIT = "e7f4517a83949f2a955df3dcaa041e939328364c"
SCORE_REUSE_MODEL = "a5fb69b3b067ff5d2d4c0beb3e6a27dda888e65fd520436cbcdb72cad1cc2bfe"
# Audited: comparison.forecast only appends an output, never drives the warm
# controller. Pin all relevant forward/runner/label/evaluation implementations.
SCORE_REUSE_SOURCES = {
    "final_v1/halfsec_joint/mission.py": "be70e6267c256d5b6a2d45fc94f9e8cd2e20c601ba2ceb08fdfa4a94016ad595",
    "final_v1/halfsec_joint/model.py": "289076ca15edb023d0b9bfcb186dc883149f419ff2a0e33249521785a62b0379",
    "final_v1/halfsec_joint/run.py": "0611168271def5429501545e700154997e9059dc81746af992deb39334485149",
    "final_v1/transformer_upgrade/model.py": "2935c4d390050dbcb87b85a996c93dc6be3860d7b07ed1218b8987350bc31fe1",
    "final_v1/transformer_upgrade/capture.py": "7b4bb8a4136b07db356a428efc8d43b1e61e8073afd142c0030b589a4b2409f4",
    "final_v1/transformer_upgrade/alerts.py": "076bc464bdf8107a3fdb25da1875d0b356096645403033438dab430772437609",
    "adaptive_joint_expanded/model.py": "cee27c8a328c39c1dd28ced632973f276ca529009dcecb37891052fd73576808",
    "drone_forecast_joint_training/solo/model.py": "44eaf88f8742915299cf6ddd52ca4e4988ddeb50197f5e970e4930fe3538c6c1",
    "fast_halfsec/runner.py": "8601f24b9d242731ba875fa4b749a5ec25dd17d5fcd3efddd84918689b27b0da",
}


def score_reuse_record(campaign, nominal, initial, frozen, deployed_record, deployed_threshold,
                       operating_threshold, warm, deployed, reuse):
    """Return verified sealed scores only for the specifically audited deployment.

    Unsupported research versions keep the original full comparison path. A
    malformed/mismatched requested prior fails rather than silently reusing it.
    Both models have already been constructed exactly as in the original path.
    """
    if reuse is None:
        return None
    if (nominal["team_commit"] != SCORE_REUSE_COMMIT or nominal["model_sha256"] != SCORE_REUSE_MODEL
            or deployed_record.get("step") != 30):
        return None
    import inspect
    from result_service.dashboard_contract.validation import canonical_hash
    from fast_halfsec import runner
    sources = (campaign.JointFlight, type(warm), campaign, type(warm.fleet),
               campaign.label_network, campaign.evaluate, type(warm.forecaster),
               type(warm.forecaster.backbone), runner)
    code = Path(campaign.__file__).resolve().parents[2]
    observed = {}
    for source in sources:
        location = Path(inspect.getsourcefile(source)).resolve()
        if not location.is_relative_to(code):
            return None
        observed[location.relative_to(code).as_posix()] = hashlib.sha256(location.read_bytes()).hexdigest()
    if observed != SCORE_REUSE_SOURCES or warm.training or deployed.training:
        return None
    previous, pin = reuse["evaluation"], reuse["pin"]
    meta = previous["metadata"]
    expected = dict(reference_controller="warm_05s", evaluated_model="joint", evaluated_step=30,
                    report_step=frozen["selected"]["step"], protocol_sha256=frozen["protocol"],
                    report_checkpoint_sha256=frozen["selected"]["sha256"],
                    reference_checkpoint_sha256=initial["sha256"], model_sha256=nominal["model_sha256"],
                    deployed_threshold=deployed_threshold, team_commit=nominal["team_commit"],
                    map_hash=nominal["map_sha256"], source_split=nominal["split"],
                    spec=nominal["spec"], threshold=operating_threshold, window=[3,7])
    if (previous["region"] != nominal["spec"]["region"] or
            any(meta.get(key) != value for key, value in expected.items()) or
            any(pin.get(key) != nominal[field] for key, field in
                (("team_commit","team_commit"),("map_hash","map_sha256"),("model_sha256","model_sha256"))) or
            not all(isinstance(pin.get(key),str) and pin[key] for key in
                    ("run_id","bundle_revision","manifest_sha256","data_sha256"))):
        raise ValueError("재사용할 예측 점수의 비행·지도·모델·평가 조건이 다릅니다.")
    return dict(evaluation=previous, pin=dict(pin), payload_sha256=canonical_hash(previous), sources=observed)


def restore_sealed_scores(reuse, row, arrays):
    """Restore only the recorded comparison channel; never interpolate scores."""
    import numpy as np
    previous = reuse["evaluation"]
    series, meta = previous["series"], previous["metadata"]
    n = len(arrays["network"])
    times = (np.arange(n)*.5).tolist()
    if (series.get("times") != times or meta["duration_s"] != row["duration_s"] or
            meta["spec"] != row["spec"] or meta["deployment"] != row["deployment"] or
            len(series.get("scores",[])) != n or len(series.get("valid",[])) != n):
        raise ValueError("기준 비행의 시각·편대·길이가 저장된 예측 점수와 다릅니다.")
    scores = series["scores"]
    if any(value is not None and (type(value) not in (int,float) or not math.isfinite(value)) for value in scores):
        raise ValueError("기존 예측 점수에 유효하지 않은 값이 있습니다.")
    if series["valid"] != [value is not None for value in scores]:
        raise ValueError("기존 예측 점수의 관측 마스크가 다릅니다.")
    frames = np.asarray(arrays["plan_frames"])
    if (frames.ndim != 1 or not np.issubdtype(frames.dtype,np.integer) or
            np.any(frames < 0) or np.any(frames >= n) or np.any(np.diff(frames) <= 0)):
        raise ValueError("기준 비행의 판단 시각이 올바르지 않습니다.")
    eligible = frames[frames+14 < n]
    stored = np.flatnonzero(np.asarray(series["valid"],bool))
    if not np.array_equal(eligible,stored):
        raise ValueError("기준 비행의 판단 시각에 대응하는 기존 예측 점수가 없습니다.")
    # The official forecast_cases excludes the terminal seven-second window.
    restored = np.full(len(frames),np.nan,dtype=np.float32)
    restored[frames+14<n] = np.asarray([scores[int(frame)] for frame in eligible],dtype=np.float32)
    if any(float(value) != scores[int(frame)] for frame,value in zip(eligible,restored[frames+14<n])):
        raise ValueError("기존 예측 점수를 원래 float32 정밀도로 복원할 수 없습니다.")
    arrays["comparison_scores"] = restored


def evaluation_path(bundle):
    from result_service.dashboard_contract.validation import confined
    bundle = Path(bundle).resolve()
    if bundle.parent.parent.name != "bundles" or not bundle.parent.name.startswith("team-"):
        raise ValueError("선택한 적용 비행의 경로를 확인할 수 없습니다.")
    return confined(bundle.parents[2] / "flight_evaluations",
                    Path(bundle.parent.name) / (bundle.name + ".json"), must_exist=False)


def reference_descriptor(root, manifest):
    if not manifest["run_id"].startswith("team-"):
        return manifest.get("regional_evaluation")
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
    ranking = np.isfinite(case["scores"]["joint"]) & (case["label"] >= 0)
    # Use the same official AP path as the research report. No new model call,
    # threshold choice, trajectory or outcome label is introduced here.
    summary = campaign.evaluate([case], "joint", threshold, with_ap=bool(ranking.any()))
    if not ranking.any():
        summary.update(average_precision=None, ranking_rows=0, ranking_positives=0)
    import inspect
    sources = {}
    for name, function in (("forecast_cases", campaign.forecast_cases), ("evaluate", campaign.evaluate)):
        location = Path(inspect.getsourcefile(function))
        sources[name] = dict(module=function.__module__, function=function.__name__,
                             sha256=hashlib.sha256(location.read_bytes()).hexdigest())
    times = case["times"].tolist()
    scores = [float(s) if math.isfinite(s) else None for s in case["scores"]["joint"]]
    events, alerts = event_rows(row["cuts"], times, scores, threshold, row["duration_s"], summary)
    return dict(region=row["spec"]["region"], metadata={**metadata, "duration_s":row["duration_s"],
                "spec":row["spec"], "threshold":threshold, "window":[3, 7],
                "ap_evaluation":dict(method="official_with_ap", sources=sources,
                                      eligible_rows=int(ranking.sum()),
                                      empty_reason=None if ranking.any() else "no_eligible_observations")},
                summary=summary, events=events, alerts=alerts,
                series=dict(times=times, scores=scores, valid=[s is not None for s in scores],
                            labels=case["label"].astype(int).tolist(), ranking_valid=ranking.tolist(),
                            reference_network=arrays["network"].astype(bool).tolist()))


def capture_reference(entry, release, context, core, card, nominal, scene, *, reuse_scores=None):
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
    reuse = score_reuse_record(campaign, nominal, initial, frozen, deployed_record, deployed_threshold,
                              operating_threshold, warm, deployed, reuse_scores)
    with ExitStack() as scope:
        if entry is not release:
            scope.enter_context(planned_map(entry, context, spec, core, card, nominal=True))
            scope.enter_context(entry.map_registry(spec["region"]))
            survey = entry.planned_eight.scene_for
        else:
            scope.enter_context(release.map_scope(spec["region"]))
            survey = (partial(demo_survey, context, card, scene)
                      if nominal["map_input_source"] == "dashboard_demonstration" else context.survey_for)
        flight = campaign.JointFlight(warm, operating_threshold, explore=False,
                                      comparison=None if reuse is not None else deployed)
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
    if reuse is not None:
        restore_sealed_scores(reuse,row,arrays)
    result = evaluate_reference(campaign, row, arrays, operating_threshold, metadata)
    if reuse is not None:
        from result_service.dashboard_contract.validation import canonical_hash
        if canonical_hash(reuse["evaluation"]) != reuse["payload_sha256"]:
            raise ValueError("평가 중 재사용할 원본 예측 점수가 바뀌었습니다.")
        verify_reference_upgrade(reuse["evaluation"],result)
        result["metadata"]["score_reuse"] = dict(method="sealed_comparison_scores",
            source_pin=reuse["pin"], evaluation_sha256=reuse["payload_sha256"], sources=reuse["sources"],
            reference_execution="unchanged_warm_05s", skipped="record_only_comparison_forward")
    return result


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


def verify_reference_upgrade(previous, current):
    """An AP supplement may add evidence, never change the existing evaluation."""
    for key in ("region", "events", "alerts"):
        if previous.get(key) != current.get(key):
            raise ValueError("보강 평가의 기존 사건·경보 기록이 원 평가와 다릅니다.")
    for section in ("metadata", "summary", "series"):
        if any(current.get(section, {}).get(key) != value for key, value in previous.get(section, {}).items()):
            raise ValueError("보강 평가의 기존 예측 점수·집계·조건이 원 평가와 다릅니다.")
    if current.get("metadata", {}).get("ap_evaluation", {}).get("method") != "official_with_ap":
        raise ValueError("원 연구 코드의 AP 평가 근거가 없습니다.")


def _embedded_reference(root, manifest):
    descriptor = manifest.get("regional_evaluation")
    if not descriptor:
        return None
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


def read_reference(root, manifest):
    original = _embedded_reference(root, manifest)
    root = Path(root)
    if root.parent.parent.name != "bundles" or not root.parent.name.startswith("team-"):
        return original
    from result_service.dashboard_contract.validation import canonical_hash, read_json
    from .team_baseline_record import pin_fields
    path = evaluation_path(root)
    if not path.exists():
        return original
    record = read_json(path)
    digest = record.pop("payload_sha256", None)
    pin = pin_fields(root, manifest)
    if (digest != canonical_hash(record) or record.get("pin") != pin
            or read_json(root/"READY", 4096)["manifest_sha256"] != pin["manifest_sha256"]):
        raise ValueError("선택한 비행과 지역 평가의 식별자 또는 검사값이 다릅니다.")
    payload = record["evaluation"]
    check_identity(payload, manifest)
    if original is not None:
        verify_reference_upgrade(original, payload)
    return payload
