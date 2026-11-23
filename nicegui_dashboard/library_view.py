"""Client-local S/B geography, flight filters, and pinned replay selection."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache, partial
from pathlib import Path
from typing import Awaitable, Callable

from nicegui import ui


_REGION_TABLE = Path(__file__).parent / "vendor/sangam/geography.js"
MODES = {"v21": "예측·재배치 적용", "nominal": "기본 · 미적용"}
METRICS = {"pdr": "PDR", "search_connected_mean": "CTR"}
SORTS = {"": "기본 순서", "recall_desc": "선행탐지율 높은 순", "recall_asc": "선행탐지율 낮은 순",
         "pdr_desc": "PDR 높은 순", "pdr_asc": "PDR 낮은 순"}
SelectFlight = Callable[[str, str], Awaitable | None]


@lru_cache(maxsize=2)
def geography(source: str) -> list[dict]:
    # Both builders publish their ordered geography table as a literal JSON array.
    if source not in ("S", "B"):
        raise ValueError("지원하는 카탈로그는 S와 B입니다.")
    text = _REGION_TABLE.read_text(encoding="utf-8-sig")
    prefix, marker, literal = text.partition("export const DEMO_MAPS =")
    if prefix.strip() or not marker:
        raise ValueError(f"{source} 지역 이름 표를 읽을 수 없습니다.")
    return json.loads(literal.strip().removesuffix(";"))


def _ratio(value):
    if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1:
        return value
    return None


@dataclass(frozen=True)
class Flight:
    run_id: str
    revision: str
    metadata: dict
    metrics: dict

    @property
    def key(self) -> tuple[str, str]:
        return self.run_id, self.revision


@dataclass(frozen=True)
class MapCard:
    region: str
    label: str
    province: str
    district: str
    flights: dict[str, Flight]
    evaluation: dict
    team_sha: str = ""

    @property
    def recall(self):
        if (self.evaluation.get("events") or 0) > 0:
            return _ratio(self.evaluation.get("recall"))
        return None

    def ratio(self, mode, metric):
        flight = self.flights.get(mode)
        return _ratio(flight.metrics.get(metric)) if flight else None


def _converted_at(row):
    try:
        return datetime.fromisoformat(row.get("exported_at") or "").timestamp()
    except (OverflowError, ValueError, OSError):
        return 0


def map_cards(catalog: dict, names: list[dict]) -> list[MapCard]:
    """Use only replayable pinned revisions. An unmatched flight is still viewable alone."""
    rows = sorted(catalog["runs"], key=lambda r: (_converted_at(r), r.get("bundle_revision", "")), reverse=True)
    regions = {}
    seen = set()
    for row in rows:
        run_id = row["run_id"]
        rev = row.get("bundle_revision") or row.get("revision")
        entry = catalog["entries"].get(run_id, {})
        if not rev or entry.get("revision") != rev or run_id in seen:
            continue
        if row.get("conflict") or row.get("artifact_kind", "episode") != "episode":
            continue
        if (row.get("export_status") or (row.get("status") or {}).get("export_status", "ready")) != "ready":
            continue
        meta = {**row, **(row.get("experiment_settings") or {}), **(row.get("metadata") or {})}
        if meta.get("adapter_id") != "final-v1" and meta.get("research_version") != "Final V1":
            continue
        if meta.get("mode") not in MODES or meta.get("split") != "demonstration":
            continue
        seen.add(run_id)
        metrics = {**(row.get("final_metrics") or {}), **(row.get("metrics") or {})}
        flight = Flight(run_id, rev, meta, metrics)
        regions.setdefault(meta["region"], []).append(flight)

    index = catalog["scope"]["flight_pairs"]
    evaluations = catalog.get("dashboard_analysis", {}).get("evaluations", {})
    records = []
    for place in names:
        region = place["region"]
        candidates = regions.get(region, [])
        if not candidates:
            continue
        pinned = {f.metadata["mode"]: f for f in candidates
                  if index.get(region, {}).get(f.metadata["mode"]) == f.run_id}
        base, applied = pinned.get("nominal"), pinned.get("v21")
        if base and applied and (base.metadata.get("paired_v21_run_id"), base.metadata.get("paired_v21_revision")) == applied.key and all(
            base.metadata.get(k) not in (None, "") and base.metadata[k] == applied.metadata.get(k)
            for k in ("region", "map_hash", "kind", "direction", "seed")
        ):
            flights = pinned
        else:
            first = candidates[0]
            flights = {first.metadata["mode"]: first}
        records.append(MapCard(region, place["label"], place["province"], place["district"], flights,
                               evaluations.get(region, {}).get("summary") or {}))
    return records


@dataclass(frozen=True)
class FilterResult:
    visible: list[MapCard]
    counts: dict[str, int]
    valid: int
    above: list[str]
    below: list[str]
    missing: list[str]


def filter_cards(cards, *, mode="v21", province="", district="", search="", metric="", order=""):
    query = search.strip().casefold()
    counts = dict.fromkeys(MODES, 0)
    visible, above, below, missing = [], [], [], []
    valid = 0
    for card in cards:
        if (province and card.province != province) or (district and card.district != district):
            continue
        if query and query not in " ".join((card.label, card.province, card.district, card.region)).casefold():
            continue
        if metric:
            base, applied = card.ratio("nominal", metric), card.ratio("v21", metric)
            if card.team_sha:
                # The public team core currently produces the applied flight only.
                # Filter on the chosen flight, but don't count a cross-version pair.
                missing.append(card.label)
                selected = base if mode == "nominal" else applied
                if selected is not None and selected <= 0.95:
                    visible.append(card)
                continue
            if base is None or applied is None:
                missing.append(card.label)
                continue
            counts["nominal"] += base <= 0.95
            counts["v21"] += applied <= 0.95
            if base <= 0.95 < applied:
                above.append(card.label)
            if applied <= 0.95 < base:
                below.append(card.label)
            valid += 1
            if (base if mode == "nominal" else applied) > 0.95:
                continue
        elif mode in card.flights:
            valid += 1
        if mode in card.flights:
            visible.append(card)
    if order in SORTS and order:
        def sort_key(card):
            value = card.recall if order.startswith("recall") else card.ratio(mode, "pdr")
            direction = -1 if order.endswith("desc") else 1
            return value is None, 0 if value is None else direction * value, card.label, card.region
        visible.sort(key=sort_key)
    return FilterResult(visible, counts, valid, above, below, missing)


def _percent(value):
    return f"{value:.2%}" if value is not None else "미산출"


class LibraryView:
    def __init__(self, catalog: dict, *, on_select: SelectFlight, on_open: SelectFlight, source: str):
        self.on_select = on_select
        self.on_open = on_open
        self.cards = map_cards(catalog, geography(source))
        self.archived = list(self.cards)
        self.by_flight = {f.key: card for card in self.cards for f in card.flights.values()}
        initial_mode = "v21" if any("v21" in c.flights for c in self.cards) else "nominal"
        self.initial_run = next((c.flights[initial_mode].key for c in self.cards if initial_mode in c.flights), None)
        self.selected = self.initial_run
        self.buttons = {}
        self.updating_district = False
        with ui.column().classes("w-full gap-3").props('data-testid=library-panel'):
            ui.label("지도 찾기").classes("text-lg font-bold")
            self.search = ui.input(label="지역 검색", placeholder="지역명 또는 영문 ID").props(
                "outlined dense clearable debounce=180 data-testid=library-search").classes("w-full")
            self.search.on_value_change(self._filter)
            with ui.grid(columns=2).classes("w-full gap-2"):
                provinces = sorted({m.province for m in self.cards})
                self.province = self._select("시·도", {"": "전체 시·도", **{s: s for s in provinces}},
                                             "", self._province_changed, "province")
                self.district = self._select("시·군·구", {"": "시·도 먼저 선택"}, "", self._filter, "district")
                self.district.disable()
                self.mode = self._select("비행 방식", MODES, initial_mode, self._filter, "mode")
                self.metric = self._select("성능 조건", {"": "전체 성능", **{k: f"{v} 95% 이하" for k,v in METRICS.items()}},
                                           "", self._filter, "metric")
            self.sort = self._select("지도 정렬", SORTS, "", self._sort, "sort")
            self.count = ui.label().classes("font-semibold").props("role=status aria-live=polite data-testid=library-count")
            self.summary = ui.column().classes("w-full gap-1 text-sm")
            self.listing = ui.column().classes("w-full gap-2 flex-nowrap").style(
                "max-height:62vh; overflow-y:auto; padding:2px").props("data-testid=library-cards")
        self._render()

    @staticmethod
    def _select(label, options, initial, callback, name):
        return ui.select(options, label=label, value=initial, on_change=callback).props(
            f"outlined dense options-dense data-testid=library-{name}").classes("w-full min-w-0")

    def _province_changed(self):
        self.updating_district = True
        try:
            selected = self.province.value
            districts = sorted({c.district for c in self.cards if c.province == selected})
            self.district.set_options({"": "전체 시·군·구" if selected else "시·도 먼저 선택",
                                       **{s: s for s in districts}}, value="")
            self.district.set_enabled(bool(selected))
        finally:
            self.updating_district = False
        return self._filter()

    def _filter(self):
        if self.updating_district:
            return None
        visible = self._render()
        active = self.by_flight.get(self.selected)
        if visible:
            target = active if active in visible else visible[0]
            return self._choose(target.flights[self.mode.value])
        return None

    def _sort(self):
        self._render()

    def select_mode_for_result(self, region, mode):
        """Show a requested finished run without choosing a different filtered card."""
        card = next(card for card in self.cards if card.region == region)
        self.updating_district = True
        try:
            self.mode.value = mode
        finally:
            self.updating_district = False
        self.selected = card.flights[mode].key
        self._render()
        return self.selected

    def show_team_results(self, records):
        """Use published metrics; keep archive pins for navigation to the original."""
        cards = []
        for card in self.archived:
            record = records.get(card.region)
            if record is None or "v21" not in card.flights:
                cards.append(card)
                continue
            flights = dict(card.flights)
            flights["v21"] = replace(flights["v21"], metrics=record["metrics"])
            cards.append(replace(card, flights=flights, evaluation={}, team_sha=record["sha"]))
        if cards != self.cards:
            self.cards = cards
            self.by_flight = {flight.key: card for card in cards for flight in card.flights.values()}
            self._render()

    def _choose(self, flight):
        if flight.key == self.selected:
            return None
        self.sync_selection(*flight.key)
        return self.on_select(*flight.key)

    def _open_card(self, flight):
        # Explicitly opening a card starts its flight at zero, even when it is active.
        self.sync_selection(*flight.key)
        return self.on_open(*flight.key)

    def sync_selection(self, run_id, revision):
        """Reflect the map's own flight buttons without reissuing a navigation action."""
        key = run_id, revision
        if key not in self.by_flight or self.selected == key:
            return
        self.selected = key
        self._highlight()

    def _highlight(self):
        active = self.by_flight.get(self.selected)
        for region, button in self.buttons.items():
            selected = active is not None and region == active.region
            button.props(f'aria-pressed={str(selected).lower()}')
            button.style("background:#eef8f7;color:#124c4d;border-color:#168078" if selected
                         else "background:white;color:#243e50;border-color:#ccd8dd")

    def _render(self):
        mode, metric, order = self.mode.value, self.metric.value, self.sort.value
        result = filter_cards(self.cards, mode=mode, province=self.province.value,
                              district=self.district.value, search=self.search.value or "", metric=metric, order=order)
        self.count.set_text(f"표시 지도 {len(result.visible)}개 / 전체 {len(self.cards)}개")
        self.summary.clear()
        if metric:
            with self.summary:
                ui.label(f"{METRICS[metric]} 95% 이하 · 같은 조건 비교 가능 {result.valid}개")
                ui.label(f"미적용 {result.counts['nominal']}곳 → 적용 {result.counts['v21']}곳").classes("font-semibold")
                with ui.expansion("기준선을 넘은 지역과 결측").props("dense").classes("w-full"):
                    for title, names in (("95% 초과로 올라감",result.above),("95% 이하로 내려감",result.below),
                                         ("같은 조건 비교에서 제외",result.missing)):
                        ui.label(f"{title} · {', '.join(names) or '없음'}")
        self.listing.clear()
        self.buttons = {}
        with self.listing:
            if not result.visible:
                ui.label("조건에 맞는 지도가 없습니다. 마지막으로 연 비행을 유지합니다.").classes("text-sm")
            for card in result.visible:
                flight = card.flights[mode]
                button = ui.button(color=None, on_click=partial(self._open_card, flight)).props(
                    f"flat no-caps align=left data-region={card.region}").classes("uav-region-card w-full")
                self.buttons[card.region] = button
                with button, ui.column().classes("w-full min-w-0 items-stretch gap-0"):
                    ui.label(card.label).classes("font-bold leading-tight")
                    ui.label(f"{card.province} · {card.district}").classes("text-xs text-slate-600")
                    if card.team_sha:
                        description = (f"팀 실행 · {card.team_sha[:7]}" if mode == "v21"
                                       else "기본 대형 · 기존 기록")
                        ui.label(description).classes("text-xs font-semibold")
                    if card.evaluation:
                        n = card.evaluation.get("events")
                        evidence = f"{card.evaluation.get('hits')}/{n}건" if n else "평가 대상 단절 없음"
                        ui.label(f"선행탐지율 {_percent(card.recall)} · {evidence}").classes("text-xs")
                    keys = dict.fromkeys([metric] + (['pdr'] if order.startswith("pdr") else []))
                    for key in filter(None, keys):
                        ui.label(f"{MODES[mode]} · {METRICS[key]} {_percent(card.ratio(mode, key))}").classes("text-xs")
        self._highlight()
        return result.visible


def render_library(catalog: dict, *, on_select: SelectFlight, source: str, on_open: SelectFlight | None = None) -> LibraryView:
    return LibraryView(catalog, on_select=on_select, on_open=on_open or on_select, source=source)
