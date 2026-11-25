"""Presentation regions, without changing the archived flights or research maps."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


SELECTION_PATH = Path(__file__).parent / "data" / "region_selection.json"


@lru_cache(maxsize=1)
def region_selection() -> dict:
    data = json.loads(SELECTION_PATH.read_text(encoding="utf-8-sig"))
    selected, excluded = data.get("selected_regions"), data.get("excluded_regions")
    if (data.get("schema_version") != 1 or data.get("source") != "B"
            or not isinstance(selected, list) or not isinstance(excluded, list)
            or not selected or any(not isinstance(key, str) or not key for key in selected + excluded)
            or len(set(selected)) != len(selected) or len(set(excluded)) != len(excluded)
            or set(selected) & set(excluded)
            or data.get("selected_maps") != len(selected)
            or data.get("source_maps") != len(selected) + len(excluded)):
        raise ValueError("표시할 지역 선택 목록의 형식이나 지역 수가 맞지 않습니다.")
    return data


def select_geography(names: list[dict], source: str) -> list[dict]:
    if source != "B":
        return names
    selected = set(region_selection()["selected_regions"])
    if selected - {row["region"] for row in names}:
        raise ValueError("표시할 지역 이름이 기존 지도 목록에 없습니다.")
    # Keep the established card order; ranking evidence is not a new default sort.
    return [row for row in names if row["region"] in selected]


def presentation_catalog(catalog: dict, source: str) -> dict:
    """Project only navigation indexes. Every retained flight/value stays unchanged."""
    if source != "B":
        return catalog
    selection = region_selection()
    selected = set(selection["selected_regions"])
    scope = catalog["scope"]
    if selected - set(scope["maps"]):
        raise ValueError("표시할 지역의 원본 비행 자료가 없습니다.")
    regions = [key for key in scope["maps"] if key in selected]
    pairs = {key: value for key, value in scope["flight_pairs"].items() if key in selected}
    run_ids = {rid for pair in pairs.values() for rid in pair.values()}
    if run_ids - set(catalog["entries"]):
        raise ValueError("표시할 지역의 기본·적용 비행 연결이 올바르지 않습니다.")
    previous_selection = scope.get("dashboard_selection") or {}
    visible_scope = {**scope, "maps": regions, "flight_pairs": pairs,
                     "dashboard_selection": {**previous_selection,
                                             "selection_id": selection["selection_id"],
                                             "available_maps": len(scope["maps"]),
                                             "selected_maps": len(regions),
                                             "original_recordings_preserved": previous_selection.get(
                                                 "original_recordings_preserved", True)}}
    for key in ("incident_replay", "forecast_evaluation"):
        if key in scope:
            detail = scope[key]
            visible_scope[key] = {**detail, "regions": [r for r in detail.get("regions", []) if r in selected]}
            if isinstance(detail.get("counts"), dict):
                visible_scope[key]["counts"] = {r: value for r, value in detail["counts"].items() if r in selected}
    result = {**catalog, "scope": visible_scope,
              "runs": [row for row in catalog["runs"] if row["run_id"] in run_ids],
              "entries": {rid: value for rid, value in catalog["entries"].items() if rid in run_ids}}
    if "comparisons" in catalog:
        result["comparisons"] = {key: value for key, value in catalog["comparisons"].items()
                                 if key.split("::", 1)[0] in run_ids}
    if "dashboard_analysis" in catalog:
        analysis = catalog["dashboard_analysis"]
        result["dashboard_analysis"] = {**analysis,
            "incidents": {rid: value for rid, value in analysis.get("incidents", {}).items() if rid in run_ids},
            "evaluations": {r: value for r, value in analysis.get("evaluations", {}).items() if r in selected}}
    if isinstance(catalog.get("visualization", {}).get("maps"), dict):
        result["visualization"] = {**catalog["visualization"], "maps": {
            r: value for r, value in catalog["visualization"]["maps"].items() if r in selected}}
    return result


def failed_presentation_regions(batch: dict | None, region_labels: dict) -> list[str]:
    return list(dict.fromkeys(row["region"] for row in (batch or {}).get("items", [])
                             if row.get("state") == "failed" and row.get("region") in region_labels))


def presentation_batch_status(batch: dict | None, region_labels: dict) -> dict | None:
    """Filter displayed job status while retaining the real batch for cancellation."""
    if not batch:
        return None
    items = [row for row in batch.get("items", []) if row.get("region") in region_labels]
    states = ("queued", "running", "completed", "failed", "unavailable", "cancelling", "cancelled")
    return {**batch, "items": items, "total": len(items),
            "region": batch.get("region") if batch.get("region") in region_labels else None,
            **{state: sum(row.get("state") == state for row in items) for state in states}}
