"""Run the connected team source and open its immutable results on the regional page."""
import asyncio
import json
import logging

from nicegui import ui

from .team_baseline_record import comparison_path, load_comparison, nominal_bundle
from .team_source import source_path, source_status
from .region_selection import failed_presentation_regions, presentation_batch_status


STATES = {"queued": "대기", "running": "실행 중", "completed": "완료", "failed": "실패", "unavailable": "실행 제외",
          "cancelling": "중단 중", "cancelled": "중단됨"}


def applied_pin(record):
    flight = (record.get("flights") or {}).get("v21")
    return (flight["run_id"], flight["revision"]) if flight else None


def manifest_metrics(manifest):
    metrics = dict(manifest.get("final_metrics") or manifest.get("metrics") or {})
    if "search_connected_mean" not in metrics and "ctr" in metrics:
        metrics["search_connected_mean"] = metrics["ctr"]
    return metrics


def file_stamp(path):
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except FileNotFoundError:
        return None


def native_card_record(store, record):
    """Read the same immutable pair used by the replay and comparison tabs."""
    pin = applied_pin(record)
    card = {"sha": record["sha"], "pin": pin, "metrics": {}}
    if not store:
        raise ValueError("선택한 비행이 등록된 결과 폴더를 확인할 수 없습니다.")
    bundle = store.joinpath("bundles", *pin)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    if ((manifest["run_id"], manifest["bundle_revision"], manifest["team_commit"]) != (*pin, record["sha"])
            or record.get("region", manifest["region"]) != manifest["region"]):
        raise ValueError("선택한 비행과 지역 카드의 버전이 다릅니다.")
    card["metrics"] = manifest_metrics(manifest)
    from .team_evaluation import evaluation_path, reference_descriptor
    nominal_ready = nominal_bundle(bundle) / "READY"
    # Snapshot before reading so a publication during this read is retried on the next poll.
    files = {p: file_stamp(p) for p in (comparison_path(bundle), nominal_ready, evaluation_path(bundle))}
    evaluation = reference_descriptor(bundle, manifest) or {}
    if evaluation.get("region") == manifest["region"]:
        card["evaluation"] = evaluation.get("summary") or {}
    definitions = manifest.get("metric_definitions") or {}
    card["metric_versions"] = {k: (definitions.get(native) or {}).get("definition_hash", "")
                               for k, native in (("pdr", "pdr"), ("search_connected_mean", "ctr"))}
    pair = load_comparison(bundle)
    if pair:
        card["nominal"] = manifest_metrics(pair["nominal"])
        card["metrics"] = manifest_metrics(pair["applied"])
        nominal_pin = (pair["nominal"]["run_id"], pair["nominal"]["bundle_revision"])
        card["nominal_pin"] = nominal_pin
        card["nominal_replay"] = files[nominal_ready] is not None
    return card, files


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
        self.selection = None
        self.display_key = None
        self.display_supplements = None
        self.view_ticket = 0
        self.editions = {}
        self.card_records = {}
        self.requested_display = None
        self.user_selection = 0
        self.batch_seen = object()
        self.cards_ready = False
        self.comparison_files = {}
        self.busy = False
        self.polling = False
        self.generation = 0
        self.updating_choices = False
        self.display_choice = None
        self.recorded_source = None
        self.job_error = ""

    def render(self):
        with ui.column().classes("w-full gap-2 rounded-lg bg-white p-3").style(
                "border:1px solid #d0dce2").props("data-testid=team-region-actions"):
            with ui.row().classes("items-center gap-3"):
                self.scope = ui.select({"selected": "선택 지역", "all": f"전체 {len(self.labels)}개 지역"},
                                       label="실행 범위", value="selected", on_change=self.update_button).props(
                    "dense outlined options-dense data-testid=team-run-scope").classes("min-w-32")
                self.start = ui.button("선택 지역 실행", on_click=self.start_run).props("no-caps data-testid=team-run-start")
                self.cancel = ui.button("현재 갱신 중단", on_click=self.cancel_batch).props(
                    "outline no-caps data-testid=team-run-cancel")
                self.cancel.set_visibility(False)
                self.retry = ui.button("실패 지역만 재시도", on_click=self.retry_failed).props(
                    "outline no-caps data-testid=team-run-retry")
                self.retry.set_visibility(False)
                self.versions = ui.select({}, label="표시할 비행 기록", on_change=self.choose_version).props(
                    "outlined dense options-dense data-testid=team-result-choice").classes("min-w-64")
                self.versions.set_visibility(False)
            self.source_note = ui.label().classes("text-sm").style("color:#32566b").props("data-testid=team-result-source")
            with ui.expansion("실행 관리").props("dense data-testid=team-run-details").classes("w-full"):
                self.eligible_note = ui.label().props("data-testid=team-run-eligibility").classes("text-sm")
                self.message = ui.label().props("role=status aria-live=polite data-testid=team-run-status").classes("text-sm")
                self.job_items = ui.column().classes("w-full gap-1")
        if self.updates is None:
            self.message.set_text("새 비행을 저장하려면 결과 폴더를 연결해 대시보드를 실행하세요.")
            self.start.disable()

    def attach(self, archive, replay):
        self.archive, self.replay = archive, replay
        self.selection = self.library.initial_run
        self.set_pending(True)
        self.code_updates.subscribe(self.update_button)
        self.update_button()
        ui.timer(0.05, self.show_selection, once=True)
        ui.timer(0.15, self.refresh, once=True)
        ui.timer(2, self.refresh)

    def set_pending(self, waiting):
        # Keep the map's measured size while its previous frame is hidden.
        if waiting:
            self.archive.classes(add="uav-regional-pending").props("inert")
        else:
            self.archive.classes(remove="uav-regional-pending").props(remove="inert")

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
        self.retry.set_enabled(available and checked and bool(failed_presentation_regions(batch, self.labels)))
        self.cancel.set_visibility(bool(batch.get("active")))
        self.cancel.set_text("갱신 중단 중" if batch.get("cancelling") else "현재 갱신 중단")
        self.cancel.set_enabled(self.updates is not None and not self.busy and bool(batch.get("active"))
                                and not batch.get("cancelling"))

    async def cancel_batch(self):
        batch = self.batch_seen if isinstance(self.batch_seen, dict) else {}
        if self.busy or not self.updates or not batch.get("active") or batch.get("cancelling"):
            return
        self.busy = True
        self.job_error = ""
        self.update_button()
        try:
            await asyncio.to_thread(self.updates.cancel, batch["id"])
            if self.requested_display and self.requested_display[1] == batch["sha"]:
                self.requested_display = None
        except (OSError, RuntimeError, ValueError) as exc:
            self.job_error = str(exc)
        finally:
            self.busy = False
            self.update_button()
            if self.job_error:
                self.message.set_text(self.job_error)
            await self.refresh()

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
            regions = failed_presentation_regions(batch, self.labels)
            if not regions:
                raise ValueError("현재 표시 지역에는 다시 실행할 실패 기록이 없습니다.")
            await asyncio.to_thread(self.updates.start_many, regions, batch["sha"])
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
        self.retry.set_visibility(bool(failed_presentation_regions(batch, self.labels)) and not batch.get("active"))
        self.job_items.clear()
        message = ""
        shown = presentation_batch_status(batch, self.labels)
        if shown:
            counts = " · ".join(f"{label} {shown.get(state, 0)}" for state, label in STATES.items()
                                if state not in ("cancelling", "cancelled") or shown.get(state))
            scope = "표시 지역 기준 · " if len(shown["items"]) != len(batch.get("items", [])) else ""
            message = f"팀 {shown['sha'][:7]} · {scope}{counts}"
            reused = sum(item.get("phase") == "reused" for item in shown["items"])
            if reused:
                message += f" · 완료 중 기존 결과 재사용 {reused}"
            if shown.get("region"):
                message += f" · {self.labels[shown['region']]}"
            with self.job_items:
                for item in shown["items"]:
                    region, state = item["region"], item["state"]
                    line = f"{self.labels.get(region, region)} · {STATES.get(state, state)}"
                    if item.get("message") and (state in ("running", "failed", "unavailable") or item.get("phase") == "reused"):
                        line += f" · {item['message']}"
                    ui.label(line).classes("text-sm")
        self.message.set_text(" · ".join(filter(None, [self.job_error, message])))

    def read_card_metrics(self, editions):
        records = {}
        comparison_files = {}
        ready = True
        for region in self.labels:
            selected = editions.get(region)
            if selected == "archive":
                continue
            if selected:
                current = next((row for row in self.updates.history(region)
                                if applied_pin(row) == tuple(selected.split("/"))), None)
            else:
                current = self.updates.current(region)
            if current and applied_pin(current):
                pin = applied_pin(current)
                store = getattr(self.updates, "output_root", None)
                try:
                    records[region], files = native_card_record(store, current)
                    comparison_files.update(files)
                except (KeyError, OSError, ValueError, TypeError) as exc:
                    logging.getLogger(__name__).warning("Cannot read selected edition for %s: %s", region, exc)
                    records[region] = {"sha": current["sha"], "metrics": {}, "pin": pin, "read_error":str(exc)}
                    ready = False
        return records, ready, comparison_files

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
                    self.coverage_sha = sha
                    self.coverage = ({r: row for r, row in coverage.items() if r in self.labels}
                                     if coverage is not None else None)
                    self.coverage_generation = generation
                    self.coverage_error = coverage_error
                    self.update_button()
            batch = await asyncio.to_thread(self.updates.batch)
            changed = self.batch_seen != batch
            previous = self.batch_seen if isinstance(self.batch_seen, dict) else {}
            updated = batch or {}
            publications_changed = (previous.get("id"), previous.get("completed")) != (
                updated.get("id"), updated.get("completed"))
            if changed:
                self.draw_batch(batch)
            pairs_changed = any(file_stamp(path) != stamp for path, stamp in self.comparison_files.items())
            if not self.cards_ready or pairs_changed or publications_changed:
                editions = dict(self.editions)
                records, ready, files = await asyncio.to_thread(self.read_card_metrics, editions)
                if editions == self.editions:
                    self.card_records, self.cards_ready, self.comparison_files = records, ready, files
                    self.library.show_team_results(records)
                else:
                    self.cards_ready = False
            if self.requested_display and batch and self.requested_display == (self.card.region, batch["sha"]):
                state = next((i["state"] for i in batch["items"] if i["region"] == self.card.region), None)
                if state == "completed":
                    self.requested_display = None
                    self.editions.pop(self.card.region, None)
                    self.selection = self.library.select_mode_for_result(self.card.region, "v21")
                elif state in ("failed", "unavailable", "cancelling", "cancelled"):
                    self.requested_display = None
            await self.show_selection()
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            self.message.set_text(f"팀 실행 상태를 읽을 수 없습니다 · {exc}")
        finally:
            self.polling = False

    async def select(self, key, restart):
        self.user_selection += 1
        self.requested_display = None
        self.selection = key
        self.update_button()
        await self.show_selection(restart=restart)
        if self.selection == key and self.display_choice and self.display_key == (key, self.display_choice):
            self.editions[self.card.region] = self.display_choice

    def replay_state(self, state):
        # Only accept a change of flight mode from the currently mounted region/edition.
        card = self.card
        if (not card or state.get("ticket") != self.view_ticket or state.get("loading")
                or state.get("read_error") or state.get("region") != card.region):
            return
        context = state.get("team_context")
        mode = state.get("mode")
        if mode not in card.flights:
            return
        flight = card.flights[mode]
        shown = state.get("run_id"), state.get("revision")
        if context:
            if not self.display_choice or self.display_choice.split("/")[0] != context:
                return
            expected = (tuple(self.display_choice.split("/")) if mode == "v21"
                        else self.card_records.get(card.region, {}).get("nominal_pin"))
        else:
            if self.display_choice not in ("archive", None):
                return
            expected = flight.key
        if shown != expected:
            return
        if state.get("manual") and self.display_choice:
            self.editions[card.region] = self.display_choice
            self.user_selection += 1
            self.requested_display = None
        if flight.key == self.selection:
            return
        self.editions[card.region] = self.display_choice
        self.selection = self.library.select_mode_for_result(card.region, mode)
        self.display_key = self.selection, self.display_choice
        self.user_selection += 1
        self.requested_display = None
        if self.recorded_source:
            self.show_recorded_version()
        else:
            self.show_archive_source(card.label, mode)
        self.update_button()

    async def choose_version(self, event):
        if self.updating_choices or not event.value or event.value == self.display_choice:
            return
        self.editions[self.card.region] = event.value
        self.user_selection += 1
        self.requested_display = None
        await self.show_selection()

    def show_recorded_version(self):
        region, sha, interval = self.recorded_source
        if self.mode == "nominal":
            source = f"표시 중 · {region} · 기본 대형 · 실행한 팀 코드 {sha[:7]}"
        else:
            source = f"표시 중 · {region} · 실행한 팀 코드 {sha[:7]}"
        if interval is not None and self.mode != "nominal":
            source += f" · 판단 주기 {interval:g}초"
        connected = self.code_updates.commit
        if connected and connected != sha:
            source += f" · 현재 연결 버전 {connected[:7]}과 다름"
        if self.source_note.text != source:
            self.source_note.set_text(source)

    def show_archive_source(self, region, mode):
        label = "기본 대형" if mode == "nominal" else "재배치 적용"
        self.source_note.set_text(f"표시 중 · {region} · 기존 {label} 비행")

    async def show_selection(self, *, restart=False):
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
        for record in history:
            pin = applied_pin(record)
            if pin:
                name = "/".join(pin)
                records[name] = record
                finished = str(record.get("finished_at") or "")[:19].replace("T", " ")
                options[name] = f"팀 {record['sha'][:7]} · {finished} UTC"
        options["archive"] = "기존 대시보드 비행"
        preferred = self.editions.get(card.region)
        active = preferred if preferred in options else next(iter(options))
        card_record = self.card_records.get(card.region)
        if active == "archive":
            if card_record is not None:
                self.card_records.pop(card.region)
                self.library.show_team_results(self.card_records)
        elif not card_record or card_record.get("read_error") or card_record["pin"] != applied_pin(records[active]):
            try:
                card_record, files = await asyncio.to_thread(native_card_record,
                                                            getattr(self.updates, "output_root", None), records[active])
            except (KeyError, OSError, ValueError, TypeError) as exc:
                selected = records[active]
                card_record = {"sha":selected["sha"], "metrics":{}, "pin":applied_pin(selected), "read_error":str(exc)}
                files = []
                self.cards_ready = False
            if epoch != self.generation or key != self.selection or preferred != self.editions.get(card.region):
                return
            self.card_records[card.region] = card_record
            self.comparison_files.update(files)
            self.library.show_team_results(self.card_records)
        self.updating_choices = True
        self.display_choice = active
        try:
            if self.versions.options != options or self.versions.value != active:
                self.versions.set_options(options, value=active)
            self.versions.set_visibility(bool(records))
        finally:
            self.updating_choices = False
        identity = (key, active)
        if active != "archive" and card_record.get("read_error"):
            self.display_key = self.recorded_source = None
            self.view_ticket += 1
            self.set_pending(True)
            self.archive.set_visibility(False)
            self.source_note.set_text(f"{card.label} · 선택한 비행 기록을 확인할 수 없습니다. {card_record['read_error']}")
            return
        nominal_pin = (card_record.get("nominal_pin") if active != "archive" and card_record.get("nominal_replay")
                       else None)
        same_view = self.display_key == identity and not restart
        supplements = (nominal_pin, active != "archive" and card_record.get("evaluation"))
        if same_view and self.display_supplements == supplements:
            if self.recorded_source:
                self.show_recorded_version()
            return
        self.display_key = identity
        self.display_supplements = supplements
        keep_view = same_view and self.recorded_source is not None and mode == "v21"
        self.recorded_source = None
        self.view_ticket += 1
        ticket = self.view_ticket
        if not keep_view:
            self.set_pending(True)
        self.archive.set_visibility(True)
        if not keep_view:
            self.source_note.set_text(f"{card.label} · 비행 기록을 불러오는 중")
        try:
            if active == "archive":
                open_record = self.replay.open_run if restart else self.replay.select_run
                state = await open_record(*key, ticket=ticket)
            else:
                record = records[active]
                pin = applied_pin(record)
                state = await self.replay.open_team(card.region, pin, record["sha"], mode, ticket, restart)
            if ticket != self.view_ticket:
                return
            if not state or state.get("read_error") or state.get("open_error"):
                raise ValueError((state or {}).get("open_error") or "비행 기록을 읽을 수 없습니다.")
            if active == "archive":
                expected = key
            else:
                expected = applied_pin(records[active]) if mode == "v21" else card_record.get("nominal_pin")
            if ((state.get("run_id"), state.get("revision")) != expected or state.get("ticket") != ticket
                    or state.get("loading") or state.get("region") != card.region or state.get("mode") != mode):
                raise ValueError("선택한 비행과 재생 자료가 일치하지 않습니다.")
            self.set_pending(False)
            if active == "archive":
                self.show_archive_source(card.label, mode)
            else:
                self.recorded_source = (card.label, record["sha"], state.get("team_interval"))
                self.show_recorded_version()
        except (OSError, ValueError, RuntimeError, KeyError, TimeoutError) as exc:
            if ticket == self.view_ticket:
                self.archive.set_visibility(False)
                if active != "archive" and mode == "nominal" and not card_record.get("nominal_replay"):
                    note = ("적용 비행의 실험 비교에서 기본 대형 수치를 확인할 수 있습니다."
                            if "nominal" in card_record else "동일 조건의 비교 기록도 없습니다.")
                    self.source_note.set_text(
                        f"{card.label} · 팀 {card_record['sha'][:7]} · 기본 대형 재생 기록 없음. {note}")
                else:
                    self.source_note.set_text(f"{card.label} · 비행을 열 수 없습니다. 지역 카드를 눌러 다시 시도하세요. {exc}")
