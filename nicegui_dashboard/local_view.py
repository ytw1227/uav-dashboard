"""NiceGUI operating console for the existing Final_V1_Team loopback service."""
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from nicegui import app, ui

from .managed_service import SERVICE_PROJECT
from .service import ServiceOperations, ServiceReadError


FIELDS = {"region": "지역", "mode": "모델", "split": "분할", "mission_status": "임무 결과",
          "kind": "간섭 조건", "direction": "배치 방향", "research_version": "연구 버전", "seed": "seed"}
SCOPES = {"representative": "지도 시연", "archive": "과거 버전·기타", "all": "전체"}
LABELS = {"nominal": "기본 대형", "v21": "V21 적용", "validation": "검증", "test": "시험",
          "train": "훈련", "completed": "임무 완료", "not_completed": "임무 미완료",
          "not_assessed": "판정 미기록", "demonstration": "시연"}
_assets_ready = False


def install_assets():
    global _assets_ready
    if not _assets_ready:
        for name in ("render.js", "map3d.js", "library.js", "geography.js"):
            path = SERVICE_PROJECT / "dashboard_ui" / name
            if path.is_file():
                app.add_static_file(url_path=f"/local-render/{name}", local_file=path)
        app.add_static_file(url_path="/local-render/local_map.js", local_file=Path(__file__).with_name("local_map.js"))
        _assets_ready = True


class Operations(ServiceOperations):
    """Only predefined operations are callable. Original service validates them."""

    def compare(self, pins, metric):
        return self._request("POST", "compare", {"runs": [{"run_id": r, "revision": v} for r, v in pins],
                                                "metric_ids": [metric]})

    def rescan(self):
        return self._request("POST", "registry/rescan", {})

    def share(self, payload):
        return self._request("POST", "share", payload)

    def flight_series(self, pin, duration, event_type, still_selected):
        """Keep only the chart clocks and metrics; never stream full-flight links to a tab."""
        rows = []
        start = 0
        last_interval = None
        while start <= duration and still_selected():
            end = min(start + 120, duration)
            response = self.timeline(*pin, start_s=start, end_s=end, at_s=start, channels="rf_frames")
            if (response["run_id"], response["bundle_revision"]) != pin:
                raise ValueError("시간 그래프의 실행·변환본이 선택과 다릅니다.")
            for frame in response["rf_frames"]:
                interval = (frame.get("interval_start_s"), frame.get("interval_end_s"))
                if last_interval is not None and interval <= last_interval:
                    continue
                compact = {k:v for k,v in frame.items() if isinstance(v, (float,int))}
                compact["metrics"] = {k:v for k,v in frame.get("metrics", {}).items()
                                      if k in SERIES_KEYS}
                rows.append(compact)
                last_interval = interval
            if end >= duration:
                break
            start = end
        if not still_selected():
            return None
        events, offset = [], 0
        while still_selected():
            page = self.events(*pin, event_type=event_type, offset=offset, limit=1500)
            events.extend(compact_events(page["events"]))
            offset += len(page["events"])
            if not page["events"] or offset >= page["total"]:
                break
        return {"rf_frames":rows,"events":events,"range":[0,duration]} if still_selected() else None


def meta(row):
    return {**row, **(row.get("experiment_settings") or {}), **(row.get("metadata") or {})}


def revision(row):
    return row.get("bundle_revision") or row.get("revision")


def event_id(event):
    return event.get("id") or event.get("event_id")


SERIES_KEYS = {"search_connected", "search_pair_reachability", "pdr_so_far", "timely_pdr_so_far",
               "information_shared_fraction"}


def compact_events(events):
    return [{k:e.get(k) for k in ("scope","type","start_s","end_s","temporal_kind")} for e in events]


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
    except (ValueError, OSError, OverflowError):
        return None


def playable(row):
    status = row.get("status") or {}
    return (not row.get("conflict") and row.get("artifact_kind") in (None, "episode")
            and (row.get("export_status") or status.get("export_status") or "ready") == "ready"
            and bool(revision(row)))


def groups_by_run(rows):
    grouped = defaultdict(list)
    for row in rows:
        if playable(row):
            grouped[row["run_id"]].append(row)
    for versions in grouped.values():
        versions.sort(key=lambda row: (timestamp(row.get("exported_at")) or 0, revision(row)), reverse=True)
    return grouped


def geography():
    path = SERVICE_PROJECT / "dashboard_ui/geography.js"
    if not path.is_file():
        return {}
    source = path.read_text(encoding="utf-8-sig")
    match = re.search(r"export const DEMO_MAPS\s*=\s*(\[.*?\]);", source, re.S)
    return {r["region"]: r["label"] for r in json.loads(match.group(1))} if match else {}


def is_demo(row, names):
    m = meta(row)
    return ((m.get("adapter_id") == "final-v1" or m.get("research_version") == "Final V1")
            and m.get("mode") in ("v21", "nominal") and m.get("split") == "demonstration"
            and (m.get("region") or m.get("map_id")) in names)


def latest(rows):
    eligible = []
    for row in rows:
        status = row.get("status") or {}
        when = timestamp(row.get("completed_at"))
        if (playable(row) and when is not None
                and (row.get("stage_status") or status.get("stage_status")) in ("completed", "complete")
                and (row.get("registration_status") or status.get("registration_status") or "registered") == "registered"):
            eligible.append((when, row["run_id"], row))
    return min(eligible, key=lambda x: (-x[0], x[1]))[2] if eligible else None


def dump(value):
    ui.label(json.dumps(value, ensure_ascii=False, indent=2)).classes("text-caption whitespace-pre-wrap break-all font-mono")


def show_error(error):
    return f"{error.code} · {error}" if isinstance(error, ServiceReadError) else str(error)


def value_text(value, unit=""):
    if value is None:
        return "미산출"
    if isinstance(value, (int, float)) and unit in ("ratio", "boolean"):
        return f"{value * 100:.2f}%"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".") + (f" {unit}" if unit else "")
    return str(value) + (f" {unit}" if unit else "")


class Console:
    """A single connected client's independent selections and pending reads."""

    def __init__(self, base, data_root=None):
        self.base = base.rstrip("/")
        self.data_root = data_root
        self.api = Operations(self.base)
        self.names = geography()
        self.rows = []
        self.versions = {}
        self.overrides = {}
        self.selected = {}
        self.pin = None
        self.manifest = None
        self.known = None
        self.catalog_signature = None
        self.follow = False
        self.hold_reason = None
        self.presenting = False
        self.new_results = set()
        self.load_generation = 0
        self.pending_pin = None
        self.pending_auto = False
        self.map_space = None
        self.polling = False
        self.connected = False
        self.compare_generation = 0
        self.query_generation = 0
        self.export_generation = 0
        self.flight_generation = 0
        self.plot_generation = 0
        self.filters = {}
        self.filtering = False
        self.jobs_result = {}
        self.summary_result = {}
        self.events_page = {}
        self.events_key = None
        self.last_query = None
        self.clock_fields = None
        self.available = []
        self.follow_candidates = []

    def render(self):
        ui.label("로컬 결과 보관소").classes("text-h5 text-weight-bold")
        with ui.row().classes("items-center"):
            self.status = ui.label("기존 서비스에 연결하는 중")
            ui.button("연결 다시 확인", on_click=self.refresh).props("outline")
            self.rescan_button = ui.button("완료 결과 재검색", on_click=self.rescan).props("outline")
            self.rescan_button.disable()
            ui.link("기존 서비스 화면", self.base + "/", new_tab=True)
        self.notice = ui.row().classes("items-center").props("data-testid=local-notice")
        self.load_status = ui.label().props('role=status')
        with ui.expansion("등록 상태").classes("w-full").props("data-testid=local-registry-details") as self.registry_info:
            self.registry_values = ui.label().classes("text-caption whitespace-pre-wrap break-all font-mono")
        with ui.expansion("서비스 실행 안내").classes("w-full"):
            if self.data_root is None:
                ui.label("설정한 주소에서 결과 서비스를 실행한 뒤 연결을 다시 확인하세요.")
            else:
                ui.label(f"NiceGUI와 함께 실행한 결과 서비스 · 결과 폴더 {self.data_root}")
            ui.label(f"서비스 주소  {self.base}")
        with ui.tabs().classes("w-full justify-start") as tabs:
            archive = ui.tab("archive", label="보관 비행")
            compare = ui.tab("compare", label="다중 비교")
            summaries = ui.tab("summaries", label="상위 집계·모델")
            jobs = ui.tab("jobs", label="작업 관리")
        tabs.on_value_change(lambda event: self.protect("운영 자료 확인") if event.value != "archive" else None)
        with ui.tab_panels(tabs, value=archive).classes("w-full"):
            with ui.tab_panel(archive):
                with ui.row().classes("w-full items-center"):
                    requested_scope = ui.context.client.request.query_params.get("scope")
                    self.scope = ui.select(SCOPES, value=requested_scope if requested_scope in SCOPES else "representative", label="검색 범위",
                                           on_change=self.scope_changed).classes("w-40")
                    self.search = ui.input("지도·실행 검색", on_change=self.filter_changed).props("clearable debounce=200")
                    self.follow_box = ui.switch("최신 완료 결과 따라가기", on_change=self.change_follow).props("data-testid=local-follow-switch")
                    ui.switch("발표 장면 고정", on_change=self.change_presentation)
                with ui.row().classes("items-center"):
                    self.follow_state = ui.label().props('role=status data-testid=local-follow-status')
                    self.resume_button = ui.button("최신 추적 재개", on_click=self.resume_follow).props("outline")
                self.show_follow_state()
                with ui.expansion("보관 조건 필터").classes("w-full"):
                    with ui.row().classes("w-full"):
                        for key, label in FIELDS.items():
                            self.filters[key] = ui.select([], label=label, on_change=self.filter_changed).props(
                                "clearable outlined dense").classes("min-w-28")
                self.library = ui.column().classes("w-full")
                self.detail = ui.column().classes("w-full")
            with ui.tab_panel(compare):
                ui.label("선택한 보관 실행을 조건·지표 정의별로 비교합니다.")
                with ui.row().classes("items-center"):
                    self.metric = ui.select({"pdr": "패킷 전달률 PDR", "search_connected_mean": "연결 유지율 CTR"},
                                            value="search_connected_mean", label="비교 지표",
                                            on_change=self.compare).classes("min-w-56")
                    ui.button("비교 선택 모두 해제", on_click=self.clear_comparison).props("flat")
                self.comparison = ui.column().classes("w-full")
                with self.comparison:
                    ui.label("보관 비행에서 비교할 실행을 선택하세요.")
            with ui.tab_panel(summaries):
                self.summaries = ui.column().classes("w-full")
            with ui.tab_panel(jobs):
                self.jobs = ui.column().classes("w-full")
        self.render_library()
        self.render_summaries()
        self.render_jobs()
        ui.timer(0.05, self.refresh, once=True)
        ui.timer(5, self.refresh)

    async def refresh(self):
        if self.polling:
            return
        self.polling = True
        try:
            result, status = await asyncio.gather(asyncio.to_thread(self.api.runs), asyncio.to_thread(self.api.status))
            rows = result["runs"]
            changed = result.get("generation") != self.catalog_signature or not self.connected
            self.connected = True
            self.rescan_button.enable()
            self.status.set_text(f"서비스 연결됨 · 등록 자료 {len(rows)}건 · 세대 {result.get('generation', '미기록')}")
            registry_label = "완료 결과 검색 중" if status.get("scanning") else "등록 상태"
            self.registry_info.set_text(registry_label + f" · 확인 필요 {len(status.get('errors', []))}건")
            registry_values = {k: status.get(k) for k in ("last_scan_at", "next_scan_at", "counts", "errors",
                                                          "raw_export_enabled", "scan_deferred_reason", "storage")}
            self.registry_values.set_text(json.dumps(registry_values, ensure_ascii=False, indent=2))
            first_catalog = self.known is None
            if changed:
                previous = self.known
                self.known = {(r["run_id"], revision(r)) for r in rows}
                self.rows = rows
                self.versions = groups_by_run(rows)
                self.catalog_signature = result.get("generation")
                self.new_results.intersection_update(self.known)
                if previous is not None:
                    self.new_results.update(self.known - previous)
                self.update_filters()
                self.render_library()
                self.summary_result = await asyncio.to_thread(self.api.summaries)
                self.render_summaries()
            jobs = await asyncio.to_thread(self.api.jobs)
            if jobs != self.jobs_result:
                self.jobs_result = jobs
                self.render_jobs()
            if self.following:
                await self.apply_latest()
            elif first_catalog and not self.presenting and not self.pin and self.available:
                await self.open_run(self.available[0], automatic=True)
        except (ServiceReadError, OSError, ValueError) as error:
            self.connected = False
            self.rescan_button.disable()
            self.status.set_text(f"서비스 연결 대기 · {show_error(error)}")
            self.jobs_result = {}
            self.render_jobs()
        finally:
            self.polling = False

    async def rescan(self):
        try:
            await asyncio.to_thread(self.api.rescan)
            ui.notify("기존 서비스에 재검색을 요청했습니다.")
            await self.refresh()
        except (ServiceReadError, ValueError) as error:
            ui.notify(show_error(error), type="negative")

    def scoped(self):
        scope = self.scope.value
        rows = [versions[0] for versions in self.versions.values()]
        baselines = [r for r in rows if is_demo(r, self.names) and meta(r).get("mode") == "nominal"]
        for index, row in enumerate(rows):
            if not is_demo(row, self.names) or meta(row).get("mode") != "v21":
                continue
            matches = [meta(r) for r in baselines if meta(r).get("paired_v21_run_id") == row["run_id"]]
            if len(matches) != 1:
                continue
            baseline = matches[0]
            for candidate in self.versions[row["run_id"]]:
                data = meta(candidate)
                if revision(candidate) == baseline.get("paired_v21_revision") and all(
                    data.get(k) not in (None, "") and data[k] == baseline.get(k)
                    for k in ("region", "map_hash", "kind", "direction", "seed")
                ):
                    rows[index] = candidate
                    break
        return [r for r in rows if scope == "all" or (is_demo(r, self.names) == (scope == "representative"))]

    def update_filters(self):
        self.filtering = True
        for key, field in self.filters.items():
            vals = sorted({str(v) for row in self.scoped() if (v := meta(row).get(key)) is not None})
            field.set_options({v: LABELS.get(v, self.names.get(v, v)) for v in vals},
                              value=field.value if field.value in vals else None)
        self.filtering = False

    async def scope_changed(self):
        self.cancel_automatic_load()
        self.filtering = True
        self.search.value = ""
        for field in self.filters.values():
            field.value = None
        self.update_filters()
        await self.filter_changed()
        if self.available and self.pin and self.pin[0] not in {r["run_id"] for r in self.available}:
            if not self.hold_reason and not self.presenting:
                await self.open_run(self.available[0], automatic=True)

    async def filter_changed(self):
        if self.filtering:
            return
        self.render_library()
        self.cancel_automatic_load()
        if self.following:
            await self.apply_latest()

    @property
    def following(self):
        return self.follow and not self.hold_reason and not self.presenting

    def cancel_automatic_load(self):
        if self.pending_auto:
            self.load_generation += 1
            self.pending_pin = None
            self.pending_auto = False
            self.load_status.set_text("")

    def show_follow_state(self):
        if self.presenting:
            text = "현재 장면 유지 · 발표 중"
        elif not self.follow:
            text = "최신 추적 꺼짐"
        elif self.hold_reason:
            text = f"현재 장면 유지 · {self.hold_reason}"
        else:
            text = "최신 결과 대기 중"
        self.follow_state.set_text(text)
        self.resume_button.set_visibility(bool(self.follow and self.hold_reason and not self.presenting))

    def protect(self, reason):
        self.cancel_automatic_load()
        if self.follow:
            self.hold_reason = reason
            self.show_follow_state()

    def change_presentation(self, event):
        self.presenting = bool(event.value)
        if self.presenting:
            self.protect("발표 장면 고정")
        self.show_follow_state()

    async def change_follow(self, event):
        self.follow = bool(event.value)
        self.cancel_automatic_load()
        self.hold_reason = "발표 장면 고정" if self.presenting and self.follow else None
        self.show_follow_state()
        if self.following:
            await self.apply_latest(notify=True)

    async def resume_follow(self):
        if self.follow and not self.presenting:
            self.hold_reason = None
            self.show_follow_state()
            await self.apply_latest(notify=True)

    async def apply_latest(self, notify=False):
        row = latest(self.follow_candidates)
        if self.following and row and self.pin != (row["run_id"], revision(row)):
            await self.open_run(row, automatic=True)
        elif not row and notify:
            ui.notify("현재 조건에 실제 완료시각이 기록된 재생 가능 비행이 없습니다.")

    def dismiss_results(self):
        self.new_results.clear()
        self.notice.clear()

    def render_notice(self):
        self.notice.clear()
        if not self.new_results:
            return
        # An explicit open may select a new revision of a manually pinned run.
        in_scope = {r["run_id"] for r in self.scoped()}
        choices = [r for r in self.rows if (r["run_id"], revision(r)) in self.new_results
                   and r["run_id"] in in_scope and playable(r) and self.matches_filters(r)]
        choices.sort(key=lambda r: (timestamp(r.get("exported_at")) or 0, revision(r)), reverse=True)
        target = latest(choices) or next(iter(choices), None)
        with self.notice:
            ui.label(f"새 결과 또는 변환본 {len(self.new_results)}건이 등록되었습니다.")
            if target:
                ui.button("새 결과 보기", on_click=lambda: self.open_new_result(target)).props("outline")
            ui.button("확인", on_click=self.dismiss_results).props("flat")

    async def open_new_result(self, row):
        self.protect("새 결과 직접 선택")
        pin = row["run_id"], revision(row)
        await self.open_run(row)
        if self.pin == pin:
            self.overrides[pin[0]] = pin[1]
            if pin[0] in self.selected:
                self.selected[pin[0]] = pin[1]
                await self.compare()
            self.new_results.discard(pin)
            self.render_library()

    def matches_filters(self, row):
        m = meta(row)
        region = m.get("region") or m.get("map_id") or "지도 미기록"
        name = self.names.get(region, region)
        corpus = " ".join(str(x or "") for x in
                          (name, region, m.get("mode"), m.get("controller"), m.get("seed"), row["run_id"]))
        search = (self.search.value or "").strip().casefold()
        return search in corpus.casefold() and all(
            not field.value or str(m.get(key)) == field.value for key, field in self.filters.items()
        )

    def render_library(self):
        self.library.clear()
        self.available = []
        self.follow_candidates = []
        groups = defaultdict(list)
        for base in self.scoped():
            rid = base["run_id"]
            if self.matches_filters(base):
                self.follow_candidates.append(base)
            active_rev = self.pin[1] if self.pin and self.pin[0] == rid else None
            chosen = self.overrides.get(rid) or self.selected.get(rid) or active_rev
            row = next((r for r in self.versions[rid] if revision(r) == chosen), base)
            m = meta(row)
            region = m.get("region") or m.get("map_id") or "지도 미기록"
            name = self.names.get(region, region)
            if not self.matches_filters(row):
                continue
            self.available.append(row)
            groups[(name, m.get("map_hash") or f"미기록 {rid}")].append(row)
        with self.library:
            ui.label(f"지도 {len(groups)}개 · 독립 실행 {len(self.available)}건 · 비교 선택 {len(self.selected)}건")
            if not groups:
                ui.label("현재 조건에서 재생할 수 있는 보관 비행이 없습니다.")
            for (name, map_hash), rows in sorted(groups.items()):
                with ui.expansion(f"{name} · {len(rows)}개 실행 · 지도 {str(map_hash)[:10]}",
                                  value=len(groups) <= 4).classes("w-full"):
                    for row in rows:
                        rid, rev = row["run_id"], revision(row)
                        m = meta(row)
                        with ui.row().classes("w-full items-center"):
                            ui.checkbox("비교", value=rid in self.selected,
                                        on_change=lambda e, r=row: self.toggle_compare(r, e.value))
                            title = f"{name} · {LABELS.get(m.get('mode'), m.get('mode') or '모델 미기록')} · seed {m.get('seed', '—')}"
                            ui.button(title, on_click=lambda r=row: self.open_run(r)).props("flat no-caps")
                            options = {revision(v): f"{revision(v)} · {v.get('exported_at') or '변환시각 미기록'}"
                                       for v in self.versions[rid]}
                            ui.select(options, value=rev, label="변환본", on_change=lambda e, r=rid: self.pick_revision(r, e.value)).classes("min-w-52")
        self.render_notice()

    async def pick_revision(self, rid, rev):
        self.protect("변환본 직접 선택")
        self.overrides[rid] = rev
        row = next(r for r in self.versions[rid] if revision(r) == rev)
        if rid in self.selected:
            self.selected[rid] = rev
        self.render_library()
        if self.pin and self.pin[0] == rid:
            await asyncio.gather(self.open_run(row), self.compare())
        else:
            await self.compare()

    async def toggle_compare(self, row, checked):
        self.protect("비교 대상 선택")
        if checked:
            self.selected[row["run_id"]] = revision(row)
        else:
            self.selected.pop(row["run_id"], None)
        self.render_library()
        await self.compare()

    async def clear_comparison(self):
        self.selected.clear()
        self.render_library()
        await self.compare()

    async def compare(self):
        self.compare_generation += 1
        token = self.compare_generation
        pins = tuple(self.selected.items())
        metric = self.metric.value
        self.comparison.clear()
        with self.comparison:
            ui.label("서비스가 조건·정의를 대조하는 중" if pins else "보관 비행에서 비교할 실행을 선택하세요.")
        if not pins:
            return
        try:
            result = await asyncio.to_thread(self.api.compare, pins, metric)
        except (ServiceReadError, ValueError) as error:
            if token == self.compare_generation:
                self.comparison.clear()
                with self.comparison:
                    ui.label(show_error(error))
            return
        if token != self.compare_generation:
            return
        self.comparison.clear()
        with self.comparison:
            counts = result["counts"]
            ui.label(f"선택 {counts['selected']}건 · 포함 {counts['included']}건 · 임무 미완료 {counts['mission_failure']}건"
                     f" · 프로세스 실패·중단 {counts['process_failure_or_interrupted']}건 · 자료 오류 {counts['data_error']}건")
            for warning in result["warnings"]:
                ui.label(warning)
            for group in result["groups"]:
                gm = group["metadata"]
                title = f"{gm.get('region') or '지역 미기록'} · {gm.get('mode') or '모델 미기록'} · {group['condition_signature'][:12]}"
                with ui.card().classes("w-full"):
                    ui.label(title).classes("text-subtitle1 text-weight-bold")
                    stats = next((v for v in group["metrics"] if v["metric_id"] == metric), None)
                    if stats:
                        unit = stats.get("unit", "")
                        ui.label(f"{stats['label']} · 표본 {stats['n']} · 균등 평균 {value_text(stats['mean'], unit)}"
                                 f" · 최악 {value_text(stats['worst'], unit)} · 중앙값 {value_text(stats['median'], unit)}")
                        if stats["values"]:
                            ui.echart({"animation": False, "tooltip": {"trigger": "item"},
                                       "grid": {"top": 12, "bottom": 28, "left": 44, "right": 20},
                                       "xAxis": {"type": "value", "scale": True, "name": unit},
                                       "yAxis": {"type": "category", "data": ["표본"]},
                                       "series": [{"type": "scatter", "data": [[v, 0] for v in stats["values"]]}]}).classes("w-full h-24")
                    else:
                        ui.label("이 그룹에 요청한 지표의 유효한 값이 없습니다.")
                    if metric == "pdr":
                        pooled = group["pooled_pdr"]
                        ui.label(f"패킷 합산 PDR {value_text(pooled['value'], 'ratio')} · 전달 {pooled['delivered']} / 생성 {pooled['generated']}")
                    for r in result["runs"]:
                        if r["run_id"] in group["run_ids"]:
                            saved = r["metrics"].get(metric, {})
                            with ui.expansion(f"{r['run_id']} · {value_text(saved.get('value'), saved.get('definition', {}).get('unit', ''))}"):
                                dump({k: r.get(k) for k in ("bundle_revision", "mission_status", "metadata", "metrics")})
            if not result["groups"]:
                ui.label("비교할 수 있는 그룹이 없습니다.")
            if result["excluded"] or result["unverified_conditions"]:
                with ui.expansion("제외 실행과 미확인 조건", value=True):
                    dump({"excluded": result["excluded"], "unverified_conditions": result["unverified_conditions"]})

    def render_summaries(self):
        self.summaries.clear()
        with self.summaries:
            if not self.connected:
                ui.label("서비스에 연결하면 저장된 상위 집계를 볼 수 있습니다.")
                return
            data = self.summary_result
            counts = data.get("counts", {})
            ui.label(f"평가 {counts.get('evaluation', 0)} · 학습 진행 {counts.get('training', 0)} · 준비 {counts.get('bootstrap', 0)}"
                     f" · 동일 원본 중복 {counts.get('duplicates_hidden', 0)}")
            ui.label("상위 집계와 요약 전용 기록은 독립 비행 표본에서 제외합니다.")
            if data.get("final_v1"):
                with ui.expansion("배포 모델 평가 원본").classes("w-full"):
                    dump(data["final_v1"])
            for record in data.get("records", []):
                with ui.expansion(record.get("label") or record["run_id"]).classes("w-full"):
                    dump(record)
            for row in self.rows:
                if row.get("artifact_kind") == "episode_summary" or row.get("export_status") == "summary_only" and row.get("artifact_kind") in (None, "episode"):
                    with ui.expansion(f"요약 전용 · {row.get('label') or row['run_id']}").classes("w-full"):
                        ui.label("위치·통신 기록이 없어 이 실행의 지도를 재생할 수 없습니다.")
                        dump(row)

    def render_jobs(self):
        self.jobs.clear()
        with self.jobs:
            caps = self.jobs_result.get("capabilities", {})
            enabled = bool(self.connected and caps.get("enabled"))
            ui.label("서비스에 연결하면 작업 상태를 확인할 수 있습니다." if not self.connected else
                     "허용된 연구 작업을 관리합니다." if enabled else "연구 작업 제어 꺼짐 · 저장 결과 조회·재생 가능")
            ui.label("연구 작업과 지도 재생은 별도로 제어합니다.")
            if not caps:
                return
            commands = {key: {"verify": "저장 추론 경로 검증", "doctor": "연구 환경 점검", "evaluate": "체크포인트 1사례 평가"}[key]
                        for key in caps.get("commands", []) if key in ("verify", "doctor", "evaluate")}
            with ui.row().classes("items-center"):
                command = ui.select(commands, value=next(iter(commands), None), label="작업").classes("min-w-48")
                checkpoint = ui.input("기존 checkpoint 상대 경로")
                button = ui.button("연구 작업 시작", on_click=lambda: self.job_dialog(command.value, checkpoint.value))
                button.set_enabled(enabled and bool(commands))
            for job in self.jobs_result.get("jobs", []):
                with ui.expansion(f"{job.get('command', '')} · {job.get('status', '')} · {job['job_id']}").classes("w-full"):
                    dump(job)
                    ui.button("이 작업 중지", on_click=lambda j=job: self.job_dialog(None, job_id=j["job_id"])).set_enabled(
                        enabled and job.get("status") in ("running", "starting"))

    async def job_dialog(self, command, checkpoint=None, job_id=None):
        with ui.dialog() as dialog, ui.card():
            ui.label(f"연구 작업 {job_id or command}을 {'중지' if job_id else '시작'}할까요?")
            with ui.row():
                ui.button("취소", on_click=lambda: dialog.submit(False)).props("flat")
                ui.button("중지" if job_id else "시작", on_click=lambda: dialog.submit(True))
        if await dialog:
            try:
                if job_id:
                    await asyncio.to_thread(self.api.stop_job, job_id)
                else:
                    await asyncio.to_thread(self.api.start_job, command, (checkpoint or "").strip())
                await self.refresh()
            except (ServiceReadError, ValueError) as error:
                ui.notify(show_error(error), type="negative")

    async def open_run(self, row, *, automatic=False):
        pin = (row["run_id"], revision(row))
        if automatic and self.pending_pin and (not self.pending_auto or self.pending_pin == pin):
            return
        if not automatic:
            self.protect("보관 비행 직접 선택")
        self.load_generation += 1
        seq = self.load_generation
        self.pending_pin, self.pending_auto = pin, automatic
        self.load_status.set_text("선택한 변환본의 지도와 첫 비행 구간을 확인하는 중")
        try:
            manifest, scene = await asyncio.gather(asyncio.to_thread(self.api.manifest, *pin),
                                                  asyncio.to_thread(self.api.scene, *pin))
            if seq != self.load_generation:
                return
            if (manifest.get("run_id"), revision(manifest)) != pin:
                raise ValueError("응답의 실행·변환본이 선택과 다릅니다.")
            map_hash = manifest.get("map_hash")
            if not map_hash or scene.get("map_hash") != map_hash:
                raise ValueError("응답의 지도 해시가 선택한 비행과 다릅니다.")
            space = (map_hash, scene.get("coordinate_system"), scene.get("bounds"))
            duration = float(manifest.get("simulation_end_s") or 0)
            fleet = {n["id"]: f"{n['id']} · {n.get('role', '')}" for n in manifest.get("fleet", [])}
            observer = next((n["id"] for n in manifest.get("fleet", []) if n.get("role") == "relay"), next(iter(fleet), None))
            peer = next((n for n in fleet if n != observer), None)
            end_s = min(duration, 15)
            request_id = f"open-{seq}"
            timeline, page = await asyncio.gather(
                asyncio.to_thread(self.api.timeline, *pin, start_s=0, end_s=end_s, at_s=0,
                                  observer=observer, peer=peer, request_id=request_id),
                asyncio.to_thread(self.api.events, *pin, start_s=0, end_s=end_s),
            )
            if seq != self.load_generation:
                return
            self.check_window(pin, request_id, timeline, page)
            camera = None
            if self.clock_fields and self.map_space == space:
                camera = await self.canvas.client.run_javascript(
                    f'(async()=> (await import("/local-render/local_map.js")).cameraView({self.canvas.id}))()')
            if seq != self.load_generation:
                return
            # Previous view remains usable until the selected immutable revision is ready.
            self.query_generation += 1
            initial_query = self.query_generation
            self.plot_generation += 1
            self.flight_generation += 1
            committed = self.flight_generation
            self.pin, self.manifest, self.map_space = pin, manifest, space
            self.event_value = self.last_query = self.events_key = None
            self.events_page = {}
            self.pending_pin, self.pending_auto = None, False
            self.load_status.set_text("")
            self.detail.clear()
            m = meta(row)
            with self.detail:
                ui.label(f"{self.names.get(m.get('region'), m.get('region') or '보관 비행')} · {LABELS.get(m.get('mode'),m.get('mode'))}").classes("text-h6")
                ui.label(f"{self.pin[0]} / {self.pin[1]} · 연구 완료 {row.get('completed_at') or '미기록'} · 변환 {row.get('exported_at') or '미기록'}").props("data-testid=local-open-pin")
                with ui.row().classes("items-center"):
                    start = ui.number("조회 시작(초)", value=0, min=0, max=duration).classes("w-28")
                    end = ui.number("조회 끝(초)", value=min(duration, 15), min=0, max=duration).classes("w-28")
                    at = ui.number("재생 시각(초)", value=0, min=0, max=duration).classes("w-28")
                    node = ui.select(fleet, value=observer, label="관측 기체").classes("min-w-36")
                    target = ui.select(fleet, value=peer, label="상대 기체").classes("min-w-36")
                    for field in (start, end, at):
                        field.on('input', lambda: self.protect("재생 시각·구간 직접 입력"), args=[])
                    for field in (node, target):
                        field.on('popup-show', lambda: self.protect("관측 기체 선택"), args=[])
                    ui.button("이 구간 보기", on_click=self.manual_query)
                self.clock_fields = (start, end, at, node, target)
                self.canvas = ui.element("canvas").classes("w-full").style("height:360px;touch-action:none;display:block").props("tabindex=0 aria-label=보관비행지도")
                self.canvas_label = ui.label().classes("text-body2")
                self.canvas.on("local-pair", self.canvas_selection, args=["detail"])
                self.canvas.on("local-time", self.canvas_time, args=["detail"])
                self.canvas.on("local-camera", lambda: self.protect("지도 시점 조작"), args=[])
                with ui.row().classes("items-center"):
                    ui.button("구간 재생·정지", on_click=lambda: self.map_command("toggle")).props("outline")
                    ui.button("2D", on_click=lambda: self.map_command("2d")).props("flat")
                    ui.button("3D", on_click=lambda: self.map_command("3d")).props("flat")
                    ui.button("전체 지도", on_click=lambda: self.map_command("reset")).props("flat")
                ui.label("핵심 지표의 시간별 변화").classes("text-subtitle1 text-weight-bold")
                with ui.row().classes("items-center"):
                    self.series = ui.select([], label="시간 그래프 지표", on_change=self.series_changed).classes("min-w-56")
                    self.series.on('popup-show', lambda: self.protect("시간 그래프 지표 선택"), args=[])
                    ui.button("전체 비행 그래프", on_click=self.full_plot).props("outline")
                    ui.button("재생 시각 주변", on_click=self.window_plot).props("flat")
                self.plot_note = ui.label()
                self.chart = ui.element("div").style("width:100%;max-width:980px;cursor:crosshair").props("aria-label=보관비행시간그래프")
                self.chart.on("local-seek", self.chart_seek, args=["detail"])
                self.chart_note = ui.label().classes("text-caption")
                self.report = ui.column().classes("w-full")
                with ui.expansion("선택 구간 내보내기 · 실행 번들").classes("w-full"):
                    ui.label("스냅샷에는 선택 시각·기체·사건과 최대 240초의 저장 구간이 포함됩니다.")
                    with ui.row():
                        ui.button("구간 스냅샷 HTML", on_click=lambda: self.export("snapshot")).props("outline")
                        ui.button("실행 번들 ZIP", on_click=lambda: self.export("bundle")).props("outline")
                    self.download = ui.column()
                    ui.label(f"기존 번들 가져오기 · {SERVICE_PROJECT} 폴더에서 실행")
                    ui.code("python -m dashboard_service.import_results <ZIP> --output-root <결과 폴더>")
                with ui.expansion("기록 시계 · 지표 정의 · 가용 기능").classes("w-full"):
                    dump({k: manifest.get(k) for k in ("clocks", "capabilities", "final_metrics", "metric_definitions")})
            old_options = self.metric.options
            options = dict(old_options)
            for mid, definition in (manifest.get("metric_definitions") or {}).items():
                if definition.get("comparable", True):
                    options[mid] = f"{definition.get('label') or mid} ({mid})"
            self.metric.set_options(options, value=self.metric.value)
            settings = {k:manifest.get(k) for k in ("adapter_id","research_version","metadata","series_definitions")}
            settings["metric_definitions"] = {k:v for k,v in manifest.get("metric_definitions", {}).items() if k in SERIES_KEYS}
            await self.canvas.client.run_javascript(f'(async()=>{{const m=await import("/local-render/local_map.js");m.setup({self.canvas.id},{self.canvas_label.id},{self.chart.id},{self.chart_note.id},{json.dumps(settings)},{json.dumps(scene)},{json.dumps(camera)});}})()', timeout=15)
            if committed == self.flight_generation and initial_query == self.query_generation:
                initial_values = (0, end_s, 0, observer, peer)
                initial = tuple(f.value for f in self.clock_fields) == initial_values
                await self.query(prepared=(request_id, timeline, page) if initial else None)
        except (ServiceReadError, OSError, TypeError, KeyError, ValueError, TimeoutError) as error:
            if seq == self.load_generation:
                self.load_status.set_text(f"선택한 자료를 열 수 없습니다 · {show_error(error)}")
        finally:
            if seq == self.load_generation:
                self.pending_pin, self.pending_auto = None, False

    @staticmethod
    def check_window(pin, request_id, timeline, page):
        if timeline.get("request_id") != request_id or any(
            (part.get("run_id"), revision(part)) != pin for part in (timeline, page)
        ):
            raise ValueError("구간 응답이 현재 실행·변환본과 일치하지 않습니다.")

    async def manual_query(self):
        self.protect("재생 구간 직접 선택")
        await self.query()

    async def map_command(self, cmd):
        if self.clock_fields:
            self.protect("재생·일시정지" if cmd == "toggle" else "지도 시점 조작")
            await self.canvas.client.run_javascript(f'(await import("/local-render/local_map.js")).command({self.canvas.id},{json.dumps(cmd)})')

    async def canvas_selection(self, e):
        if not self.clock_fields or e.sender is not self.canvas:
            return
        self.protect("지도 기체 선택")
        detail = e.args.get("detail") or {}
        node, peer = self.clock_fields[-2:]
        if detail.get("node") in node.options:
            node.value = detail["node"]
        if detail.get("peer") in peer.options:
            peer.value = detail["peer"]
        if node.value == peer.value:
            peer.value = next((k for k in peer.options if k != node.value), None)
        await self.query()

    async def canvas_time(self, e):
        detail = e.args["detail"]
        if (self.clock_fields and e.sender is self.canvas
                and detail.get("request_seq") == self.query_generation):
            self.clock_fields[2].value = detail["t"]
            await self.query(resume=bool(detail.get("resume")))

    async def chart_seek(self, e):
        if self.clock_fields and e.sender is self.chart:
            self.protect("그래프 시각 선택")
            t = min(max(0,float(e.args["detail"]["t"])), float(self.manifest["simulation_end_s"]))
            start, end, at = self.clock_fields[:3]
            if t < start.value or t > end.value:
                start.value, end.value = 0, float(self.manifest["simulation_end_s"])
            at.value = t
            await self.query()

    async def series_changed(self, event):
        if self.clock_fields and event.value:
            await self.canvas.client.run_javascript(f'(await import("/local-render/local_map.js")).metric({self.canvas.id},{json.dumps(event.value)})')

    async def window_plot(self):
        self.protect("그래프 구간 선택")
        self.plot_generation += 1
        self.plot_note.set_text("")
        await self.canvas.client.run_javascript(f'(await import("/local-render/local_map.js")).windowPlot({self.canvas.id})')

    async def full_plot(self):
        if not self.last_query:
            return
        self.protect("전체 비행 그래프 선택")
        self.plot_generation += 1
        seq = self.plot_generation
        canvas = self.canvas
        self.plot_note.set_text("전체 비행의 저장 RF 구간을 읽고 있습니다.")
        try:
            cached = await canvas.client.run_javascript(f'(async()=> (await import("/local-render/local_map.js")).cachedPlot({canvas.id}))()')
            if seq != self.plot_generation:
                return
            if not cached:
                is_final = (self.manifest.get("adapter_id") == "final-v1" or self.manifest.get("research_version") == "Final V1")
                result = await asyncio.to_thread(self.api.flight_series, self.pin, float(self.manifest["simulation_end_s"]),
                                                 "persistent_search_outage" if is_final else None,
                                                 lambda: seq == self.plot_generation and not canvas.is_deleted)
                if not result or seq != self.plot_generation:
                    return
                await canvas.client.run_javascript(f'(await import("/local-render/local_map.js")).fullPlot({canvas.id},{json.dumps(result)})', timeout=18)
            if seq == self.plot_generation:
                self.plot_note.set_text("전체 비행 · 그래프를 누르면 그 시각으로 이동합니다.")
        except (TimeoutError, ServiceReadError, ValueError) as error:
            if seq == self.plot_generation:
                self.plot_note.set_text(show_error(error))

    async def query(self, *, resume=False, prepared=None):
        if not self.clock_fields:
            return
        self.query_generation += 1
        seq = self.query_generation
        pin = self.pin
        fields = self.clock_fields
        self.last_query = None
        self.report.clear()
        with self.report:
            ui.label("선택한 시각·기체의 기록을 조회하는 중")
        try:
            a,b,t = (float(f.value) for f in fields[:3])
            observer, peer = (f.value for f in fields[-2:])
            if not 0 <= a <= t <= b <= float(self.manifest["simulation_end_s"]):
                raise ValueError("조회 시작 ≤ 재생 시각 ≤ 조회 끝을 비행 시간 범위 안에서 지정하세요.")
            if observer and observer == peer:
                raise ValueError("서로 다른 기체를 선택하세요.")
            # The selected interval is also the export/event interval. Fetch the
            # dense radio frames around the playhead; continue from its boundary.
            left, right = max(a, t-12), min(b, t+30)
            key = (pin, a, b)
            if prepared is not None:
                request_id, timeline, page = prepared
            else:
                request_id = str(seq)
                read = asyncio.to_thread(self.api.timeline, *pin, start_s=left, end_s=right, at_s=t,
                                         observer=observer, peer=peer, request_id=request_id)
                if self.events_key == key:
                    page = self.events_page
                    timeline = await read
                else:
                    timeline, page = await asyncio.gather(read, asyncio.to_thread(self.api.events, *pin, start_s=a, end_s=b))
            if seq != self.query_generation:
                return
            self.check_window(pin, request_id, timeline, page)
            self.last_query = (a,b,t,observer,peer)
            self.events_page = page
            self.events_key = key
            self.report.clear()
            at_state = timeline.get("at", {})
            with self.report:
                state = (at_state.get("link_frame") or {}).get("metrics", {}).get("search_connected")
                ui.label(f"{t:g}초 · " + {1:"수색망 연결",0:"수색망 단절"}.get(state, "현재 RF 구간 미기록"))
                ui.label(f"{observer or '—'} → {peer or '—'} · 현재까지 공개된 최신 관측 {len(at_state.get('observations') or [])}건 · "
                         f"구간에 걸친 사건 {page['total']}건")
                options = {event_id(ev): f"{ev.get('start_s')}초 · {ev.get('type')} · {event_id(ev)}" for ev in page["events"] if event_id(ev)}
                previous = getattr(self, "event_value", None)
                self.event_value = previous if previous in options else None
                self.event_select = ui.select(options, value=self.event_value, label="구간 사건", on_change=self.event_changed).props("clearable").classes("w-full")
                with ui.expansion("해당 시각의 공개 관측·판단·패킷 구간").classes("w-full"):
                    dump(at_state)
            view = {k: timeline.get(k, []) for k in ("positions", "rf_frames", "decisions")}
            view.update({"node":observer, "peer":peer, "t":t, "start":a, "end":b, "cache_end":right, "resume":resume,
                         "request_seq":seq,
                         "chart_range":[left,right], "events":compact_events(page["events"]),
                         "event": next((e for e in page["events"] if event_id(e) == self.event_value), None)})
            chart_options = await self.canvas.client.run_javascript(f'(async()=> (await import("/local-render/local_map.js")).show({self.canvas.id},{json.dumps(view)}))()', timeout=15)
            if seq == self.query_generation and chart_options and self.series.options != chart_options["options"]:
                self.series.set_options(chart_options["options"], value=chart_options["selected"])
        except (TypeError, ValueError, ServiceReadError, TimeoutError) as error:
            if seq == self.query_generation:
                self.last_query = None
                self.report.clear()
                with self.report:
                    ui.label(show_error(error))

    async def event_changed(self, e):
        if e.sender is not self.event_select:
            return
        self.protect("사건 선택")
        self.event_value = e.value
        if self.clock_fields:
            ev = next((r for r in self.events_page.get("events", []) if event_id(r) == e.value), None)
            if ev:
                self.clock_fields[2].value = max(float(self.clock_fields[0].value), ev["start_s"])
            await self.query()

    async def export(self, kind):
        if not self.last_query or not self.pin:
            ui.notify("먼저 유효한 시각 구간을 조회하세요.")
            return
        pin = self.pin
        self.export_generation += 1
        generation = self.export_generation
        a,b,t,node,peer = self.last_query
        seq = self.query_generation
        try:
            if kind == "snapshot":
                current = await self.canvas.client.run_javascript(
                    f'(async()=> (await import("/local-render/local_map.js")).playbackTime({self.canvas.id}))()')
                if seq != self.query_generation or self.pin != pin:
                    return
                if isinstance(current, (float, int)) and a <= current <= b:
                    t = current
            if b-a > 240:
                a,b = max(0,t-120), min(float(self.manifest["simulation_end_s"]),t+120)
            request = {"format":kind, "run_id":pin[0], "revision":pin[1], "window_s":[a,b], "time_s":t,
                       "selected_node":node, "selected_peer":peer, "selected_event_id":self.event_value}
            result = await asyncio.to_thread(self.api.share, request)
            url = result.get("download_url", "")
            if not re.fullmatch(r"/api/downloads/[a-f0-9]{32}\.(html|zip)", url):
                raise ValueError("다운로드 주소를 확인할 수 없습니다.")
            if pin == self.pin and seq == self.query_generation and generation == self.export_generation:
                self.download.clear()
                with self.download:
                    ui.link(f"{a:g}–{b:g}초 고정 스냅샷 받기" if kind == "snapshot" else "검증된 전체 실행 ZIP 받기",
                            self.base + url, new_tab=True)
                    if kind == "snapshot":
                        ui.label("선택한 구간의 경계를 확인하기 위한 앞뒤 위치 표본이 함께 저장될 수 있습니다.")
        except (ServiceReadError, ValueError, TimeoutError) as error:
            if pin == self.pin and seq == self.query_generation and generation == self.export_generation:
                ui.notify(show_error(error), type="negative")


def render_local(base_url="http://127.0.0.1:8765", data_root=None):
    install_assets()
    view = Console(base_url, data_root=data_root)
    view.render()
    return view
