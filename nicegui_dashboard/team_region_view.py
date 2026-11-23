"""Run the connected team source and open its immutable results on the regional page."""
import asyncio

from nicegui import ui

from .local_view import Console, value_text
from .team_source import source_path, source_status


STATES = {"queued": "대기", "running": "실행 중", "completed": "완료", "failed": "실패", "unavailable": "실행 제외"}
RESULT_METRICS = (
    ("search_connected_mean", "수색망 연결 유지율 CTR", "ratio"),
    ("pdr", "패킷 전달률 PDR", "ratio"),
    ("timely_pdr", "적시 전달률", "ratio"),
    ("persistent_onsets", "수색망 단절 횟수", "회"),
    ("persistent_cut_seconds", "지속 단절 누적시간", "s"),
)


def applied_pin(record):
    flight = (record.get("flights") or {}).get("v21")
    return (flight["run_id"], flight["revision"]) if flight else None


def manifest_metrics(manifest):
    metrics = dict(manifest.get("final_metrics") or manifest.get("metrics") or {})
    if "search_connected_mean" not in metrics and "ctr" in metrics:
        metrics["search_connected_mean"] = metrics["ctr"]
    return metrics


class RegionalRuns:
    def __init__(self, updates, library, service_url, code_updates):
        self.updates = updates
        self.code_updates = code_updates
        self.coverage = None
        self.coverage_sha = None
        self.coverage_generation = None
        self.coverage_error = ""
        self.library = library
        self.labels = {card.region: card.label for card in library.cards}
        self.viewer = Console(service_url)
        self.selection = None
        self.display_key = None
        self.local_active = False
        self.preferred = None
        self.requested_display = None
        self.user_selection = 0
        self.batch_seen = object()
        self.cards_ready = False
        self.busy = False
        self.polling = False
        self.generation = 0
        self.updating_choices = False
        self.display_choice = None
        self.recorded_source = None
        self.job_error = ""

    def render(self):
        with ui.column().classes("w-full gap-2 rounded-lg border bg-white p-3").props("data-testid=team-region-actions"):
            with ui.row().classes("items-center gap-3"):
                self.scope = ui.select({"selected": "선택 지역", "all": f"전체 {len(self.labels)}개 지역"},
                                       label="실행 범위", value="selected", on_change=self.update_button).props(
                    "dense outlined options-dense data-testid=team-run-scope").classes("min-w-32")
                self.start = ui.button("선택 지역 실행", on_click=self.start_run).props("no-caps data-testid=team-run-start")
                self.retry = ui.button("실패 지역만 재시도", on_click=self.retry_failed).props(
                    "outline no-caps data-testid=team-run-retry")
                self.retry.set_visibility(False)
            self.message = ui.label().props("role=status aria-live=polite data-testid=team-run-status").classes("text-sm")
            self.eligible_note = ui.label().props("data-testid=team-run-eligibility").classes("text-sm")
            with ui.expansion("지역별 진행 상황").props("dense data-testid=team-run-details").classes("w-full") as self.job_list:
                self.job_items = ui.column().classes("w-full gap-1")
            self.job_list.set_visibility(False)
            self.versions = ui.select({}, label="표시할 비행 기록", on_change=self.choose_version).props(
                "outlined dense options-dense data-testid=team-result-choice").classes("w-full max-w-lg")
            self.versions.set_visibility(False)
            self.source_note = ui.label().classes("text-sm").props("data-testid=team-result-source")
        with ui.column().classes("w-full bg-white rounded-lg p-4 gap-3").props("data-testid=team-recorded-flight") as self.local:
            self.summary = ui.row().classes("items-start gap-5")
            self.viewer.load_status = ui.label().props("role=status")
            self.viewer.detail = ui.column().classes("w-full")
        self.local.set_visibility(False)
        if self.updates is None:
            self.message.set_text("새 비행을 저장하려면 결과 폴더를 연결해 대시보드를 실행하세요.")
            self.start.disable()

    def attach(self, archive, replay):
        self.archive, self.replay = archive, replay
        self.selection = self.library.initial_run
        self.code_updates.subscribe(self.update_button)
        self.update_button()
        ui.timer(0.15, self.refresh, once=True)
        ui.timer(2, self.refresh)

    @property
    def card(self):
        return self.library.by_flight.get(self.selection)

    @property
    def mode(self):
        return next((key for key, flight in self.card.flights.items() if flight.key == self.selection), None) if self.card else None

    def update_button(self):
        label = f"전체 {len(self.labels)}개 지역" if self.scope.value == "all" else (self.card.label if self.card else "선택 지역")
        self.start.set_text(f"{label} 새 비행 실행")
        batch = self.batch_seen if isinstance(self.batch_seen, dict) else {}
        available = self.updates is not None and not self.busy and not batch.get("active") and not self.code_updates.syncing
        checked = (self.coverage_generation == self.code_updates.generation and
                   self.coverage_sha == self.code_updates.commit and self.coverage is not None)
        possible = False
        note = self.coverage_error or ("실행 가능 지역 확인 중" if self.code_updates.commit else "팀 코드 연결 확인 중")
        if self.code_updates.syncing:
            note = "팀 코드 갱신 중"
        elif checked:
            if self.scope.value == "all":
                eligible = sum(bool(self.coverage.get(region, {}).get("eligible")) for region in self.labels)
                note = f"연결 버전 {self.coverage_sha[:7]} · 실행 가능 {eligible}개 / 전체 {len(self.labels)}개"
                possible = eligible > 0
            elif self.card:
                row = self.coverage.get(self.card.region) or {}
                possible = bool(row.get("eligible"))
                note = f"연결 버전 {self.coverage_sha[:7]} · {self.card.label} · " + ("실행 가능" if possible else (row.get("reason") or "이 버전에 실행용 지도가 없습니다."))
        self.eligible_note.set_text(note)
        self.start.set_enabled(available and possible)
        self.retry.set_enabled(available and checked)

    async def start_run(self):
        if (self.busy or not self.updates or not self.card or self.code_updates.syncing
                or self.coverage is None or self.coverage_sha != self.code_updates.commit
                or self.coverage_generation != self.code_updates.generation):
            return
        displayed_sha = self.coverage_sha
        generation = self.coverage_generation
        selected, click = self.card.region, self.user_selection
        regions = list(self.labels) if self.scope.value == "all" else [selected]
        if not any(self.coverage.get(region, {}).get("eligible") for region in regions):
            return
        self.busy = True
        self.job_error = ""
        self.update_button()
        try:
            version = await asyncio.to_thread(source_status, source_path())
            if self.code_updates.syncing or generation != self.code_updates.generation:
                raise ValueError("팀 코드가 갱신되었습니다. 표시된 버전과 실행 범위를 확인하고 다시 실행하세요.")
            if version["commit"] != displayed_sha:
                self.code_updates.observed(version["commit"])
                raise ValueError("연결 버전이 달라졌습니다. 새 실행 가능 지역을 확인하고 다시 실행하세요.")
            await asyncio.to_thread(self.updates.start_many, regions, displayed_sha)
            if click == self.user_selection:
                self.requested_display = (selected, displayed_sha)
        except (OSError, RuntimeError, ValueError) as exc:
            self.job_error = str(exc)
        finally:
            self.busy = False
            self.batch_seen = object()
            self.update_button()
            await self.refresh()

    async def retry_failed(self):
        if self.busy or not self.updates or self.code_updates.syncing or self.coverage is None:
            return
        self.busy = True
        self.job_error = ""
        self.update_button()
        click = self.user_selection
        batch = self.batch_seen if isinstance(self.batch_seen, dict) else {}
        selected = self.card.region if self.card else None
        try:
            await asyncio.to_thread(self.updates.retry)
            if click == self.user_selection and any(i["region"] == selected and i["state"] == "failed"
                                                   for i in batch.get("items", [])):
                self.requested_display = (selected, batch["sha"])
        except (OSError, RuntimeError, ValueError) as exc:
            self.job_error = str(exc)
        finally:
            self.busy = False
            self.batch_seen = object()
            self.update_button()
            await self.refresh()

    def draw_batch(self, batch):
        self.batch_seen = batch
        self.update_button()
        self.retry.set_visibility(bool(batch and batch.get("failed") and not batch.get("active")))
        self.job_list.set_visibility(bool(batch))
        self.job_items.clear()
        message = ""
        if batch:
            counts = " · ".join(f"{STATES[state]} {batch.get(state, 0)}" for state in STATES)
            message = f"팀 {batch['sha'][:7]} · {counts}"
            reused = sum(item.get("phase") == "reused" for item in batch.get("items", []))
            if reused:
                message += f" · 완료 중 기존 결과 재사용 {reused}"
            if batch.get("region"):
                message += f" · {self.labels.get(batch['region'], batch['region'])}"
            with self.job_items:
                for item in batch.get("items", []):
                    region, state = item["region"], item["state"]
                    line = f"{self.labels.get(region, region)} · {STATES.get(state, state)}"
                    if item.get("message") and (state in ("running", "failed", "unavailable") or item.get("phase") == "reused"):
                        line += f" · {item['message']}"
                    ui.label(line).classes("text-sm")
        self.message.set_text(" · ".join(filter(None, [self.job_error, message])))

    def read_card_metrics(self):
        records = {}
        ready = True
        for region in self.labels:
            current = self.updates.current(region)
            if current and applied_pin(current):
                records[region] = {"sha": current["sha"], "metrics": {}, "pin": applied_pin(current)}
        if records:
            runs = self.viewer.api.runs()["runs"]
            by_pin = {(row["run_id"], row.get("bundle_revision")): row for row in runs}
            for record in records.values():
                row = by_pin.get(record["pin"])
                if row is None:  # The result registry may still be scanning the new bundle.
                    ready = False
                    continue
                record["metrics"] = manifest_metrics(row)
        return records, ready

    async def refresh(self):
        if not self.updates or self.polling:
            return
        self.polling = True
        try:
            sha = self.code_updates.commit
            generation = self.code_updates.generation
            if sha and not self.code_updates.syncing and self.coverage_generation != generation:
                try:
                    coverage = await asyncio.to_thread(self.updates.coverage, sha, refresh=True)
                    coverage_error = ""
                except (OSError, RuntimeError, ValueError) as exc:
                    coverage = None
                    coverage_error = f"실행 가능 지역 확인 필요 · {exc}"
                if sha == self.code_updates.commit and generation == self.code_updates.generation and not self.code_updates.syncing:
                    self.coverage_sha, self.coverage = sha, coverage
                    self.coverage_generation = generation
                    self.coverage_error = coverage_error
                    self.update_button()
            batch = await asyncio.to_thread(self.updates.batch)
            changed = self.batch_seen != batch
            previous_count = self.batch_seen.get("completed", 0) if isinstance(self.batch_seen, dict) else 0
            if changed:
                self.draw_batch(batch)
            if not self.cards_ready or (changed and (batch or {}).get("completed", 0) != previous_count):
                records, self.cards_ready = await asyncio.to_thread(self.read_card_metrics)
                self.library.show_team_results(records)
            if self.requested_display and batch and self.requested_display == (self.card.region, batch["sha"]):
                state = next((i["state"] for i in batch["items"] if i["region"] == self.card.region), None)
                if state == "completed":
                    self.requested_display = None
                    self.preferred = None
                    self.selection = self.library.select_mode_for_result(self.card.region, "v21")
                elif state in ("failed", "unavailable"):
                    self.requested_display = None
            await self.show_selection()
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            self.message.set_text(f"팀 실행 상태를 읽을 수 없습니다 · {exc}")
        finally:
            self.polling = False

    async def select(self, key, restart):
        self.user_selection += 1
        self.requested_display = None
        previous = self.selection
        self.selection = key
        if previous != key:
            self.preferred = None
        self.update_button()
        await self.show_selection(restart=restart, navigate=True)

    def archive_state(self, key):
        # Ignore events emitted by the still-mounted archive while a new flight is visible.
        if self.local_active or key == self.selection or not self.card or key not in self.library.by_flight:
            return
        if self.library.by_flight[key].region != self.card.region:
            return
        self.selection = key
        self.library.sync_selection(*key)
        self.preferred = "archive"
        self.user_selection += 1
        self.requested_display = None
        self.update_button()

    async def choose_version(self, event):
        if self.updating_choices or not event.value or event.value == self.display_choice:
            return
        self.preferred = event.value
        self.user_selection += 1
        self.requested_display = None
        await self.show_selection(navigate=True)

    def show_recorded_version(self):
        region, sha, interval = self.recorded_source
        source = f"표시 중 · {region} · 실행한 팀 코드 {sha[:7]}"
        if interval is not None:
            source += f" · 판단 주기 {interval:g}초"
        connected = self.code_updates.commit
        if connected and connected != sha:
            source += f" · 현재 연결 버전 {connected[:7]}과 다름"
        if self.source_note.text != source:
            self.source_note.set_text(source)

    async def show_selection(self, *, restart=False, navigate=False):
        card = self.card
        if not card:
            return
        epoch = self.generation = self.generation + 1
        key = self.selection
        mode = self.mode
        history = await asyncio.to_thread(self.updates.history, card.region) if self.updates else []
        if epoch != self.generation or key != self.selection:
            return
        options, records = {}, {}
        if mode == "v21":
            for record in history:
                pin = applied_pin(record)
                if pin:
                    name = "/".join(pin)
                    records[name] = record
                    options[name] = f"팀 {record['sha'][:7]} · {str(record.get('finished_at') or '')[:19]}"
        options["archive"] = "기존 대시보드 비행"
        active = self.preferred if self.preferred in options else next(iter(options))
        self.updating_choices = True
        self.display_choice = active
        try:
            if self.versions.options != options or self.versions.value != active:
                self.versions.set_options(options, value=active)
            self.versions.set_visibility(bool(records))
        finally:
            self.updating_choices = False
        identity = (key, active)
        if self.display_key == identity and not restart:
            if self.recorded_source:
                self.show_recorded_version()
            return
        self.display_key = identity
        self.recorded_source = None
        self.viewer.load_generation += 1
        self.viewer.query_generation += 1
        self.viewer.plot_generation += 1
        self.viewer.flight_generation += 1
        self.viewer.clock_fields = None
        self.viewer.detail.clear()
        self.local_active = active != "archive"
        self.archive.set_visibility(not self.local_active)
        self.local.set_visibility(self.local_active)
        self.summary.clear()
        if not self.local_active:
            original = "기본 대형" if mode == "nominal" else "재배치 적용"
            note = f"표시 중 · {card.label} · 기존 {original} 비행"
            if history and mode == "nominal":
                note += " · 새 팀 비행은 ‘예측·재배치 적용’에서 볼 수 있습니다."
            self.source_note.set_text(note)
            if restart:
                await self.replay.open_run(*key)
            elif navigate or self.replay.state.get("run_id") not in (None, key[0]):
                await self.replay.select_run(*key)
            return
        record = records[active]
        pin = applied_pin(record)
        self.replay.client.run_javascript("window.uavReplay?.suspend()")
        self.source_note.set_text(f"표시 중 · {card.label} · 팀 실행 결과 확인 중")
        self.viewer.load_status.set_text("기록된 실행 버전과 지도를 확인하는 중")
        try:
            # The commit shown beside the flight must be from this exact recorded bundle.
            manifest = await asyncio.to_thread(self.viewer.api.manifest, *pin)
            if identity != self.display_key:
                return
            if (manifest.get("team_commit") != record["sha"] or manifest.get("region") != card.region
                    or (manifest.get("run_id"), manifest.get("bundle_revision")) != pin):
                raise ValueError("저장된 팀 버전·지역·실행 번호가 선택한 결과와 다릅니다.")
            self.recorded_source = (card.label, manifest["team_commit"], manifest.get("decision_interval_s"))
            self.show_recorded_version()
            metrics = manifest_metrics(manifest)
            with self.summary:
                for metric, label, unit in RESULT_METRICS:
                    if metric in metrics:
                        with ui.column().classes("gap-0"):
                            ui.label(label).classes("text-caption")
                            ui.label(value_text(metrics[metric], unit)).classes("text-subtitle1 font-semibold")
            await self.viewer.open_run(manifest, full_flight=True, heading=f"{card.label} · 팀 재배치 정책 비행")
            if identity == self.display_key and self.viewer.pin != pin:
                self.display_key = None
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            if identity == self.display_key:
                self.viewer.load_status.set_text(f"이 비행을 열 수 없습니다 · {exc}")
                self.display_key = None
