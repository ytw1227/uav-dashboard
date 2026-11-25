"""Bind the dashboard recorder to the entry point selected by the pinned team CLI."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch

from .team_demo import resolve_demo_scene


def selected_entry(fly, release, legacy_mode):
    if hasattr(fly, "SUBMISSION_MODE"):
        from final_v1.halfsec_main import submission
        if fly.SUBMISSION_MODE != submission.MODE or submission.MODE != "halfsec_planned_eight":
            raise ValueError("새 팀 기본 제어기의 실행·기록 계약을 확인할 수 없습니다.")
        return submission, "SubmissionFlight", submission.MODE
    return release, "MainFlight", legacy_mode


@contextmanager
def planned_map(entry, context, spec, core, card, nominal):
    """Provide an authenticated scene to the team's unchanged eight-aircraft planner."""
    planner = entry.planned_eight
    region = spec["region"]
    registered = dict(context.registry())
    if entry.EXPANDED_REGISTRY.is_file():
        registered.update(planner.registry())
    item = registered.get(region)
    if item and item["sha256"] != card["sha256"]:
        raise ValueError("팀의 비행 전 배치 지도와 지역 카드의 지도 식별값이 다릅니다.")
    location = (planner.LEGACY / item["path"]).resolve() if item else None
    source = "team_registry"
    role = item.get("study_role") if item else None
    if location is None or not location.is_file() or role == "legacy_protected":
        location = resolve_demo_scene(core, card)
        source = "dashboard_demonstration"
    # scene_for independently hashes the input before building the survey and radio scene.
    selected = {**(item or {}), "key": region, "path": str(location), "sha256": card["sha256"]}
    def scope(key):
        if key != region:
            raise ValueError("요청한 시연 지역과 준비된 비행 지도가 다릅니다.")
        return patch.object(planner, "registry", lambda: {region: selected})
    def no_plan(key, direction):
        if (key, direction) != (region, spec["direction"]):
            raise ValueError("기본 대형의 지도 또는 비행 방향이 일치하지 않습니다.")
        return dict(region=key, direction=direction, map_sha256=card["sha256"], station=None,
                    windows=[], reason="nominal_no_relocation")
    planner.scene_for.cache_clear()
    try:
        with patch.object(entry, "map_registry", scope), (
                patch.object(planner, "plan_for", no_plan) if nominal else nullcontext()):
            yield dict(scene=Path(location), map_sha=card["sha256"], source_study_role=role,
                       map_input_source=source, split=role if source=="team_registry" else "external_demonstration")
    finally:
        planner.scene_for.cache_clear()
