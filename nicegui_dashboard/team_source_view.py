"""Update the configured checkout from the regional dashboard."""
import asyncio

from nicegui import ui

from .team_source import TeamSourceError, source_path, source_status, sync_source


class TeamCodeUpdates:
    """Concurrent clicks from different web tabs share the same Git operation."""

    def __init__(self):
        self.task = None
        self.commit = None
        self.generation = 0
        self.syncing = False
        self.listeners = set()

    def subscribe(self, callback):
        self.listeners.add(callback)
        ui.context.client.on_delete(lambda: self.listeners.discard(callback))

    def notify(self):
        for callback in tuple(self.listeners):
            callback()

    def observed(self, commit):
        if self.commit != commit:
            self.commit = commit
            self.generation += 1
            self.notify()

    async def _sync(self):
        try:
            info = await asyncio.to_thread(sync_source, source_path())
            self.commit = info["commit"]
            self.generation += 1  # An unchanged commit can now have repaired local map assets.
            return info
        finally:
            self.syncing = False
            self.notify()

    async def sync(self):
        if self.task is None or self.task.done():
            self.syncing = True
            self.task = asyncio.create_task(self._sync())
            self.notify()
        return await asyncio.shield(self.task)


def render_team_code(updates):
    requested = False
    busy = False

    def show_status(state, message):
        status.props(f'data-state={state}')
        status.set_text(message)

    def show_version():
        if updates.commit:
            version.set_text(f"연결 버전 {updates.commit[:7]}")
        button.set_enabled(not busy and not updates.syncing)

    async def load_version():
        generation = updates.generation
        try:
            info = await asyncio.to_thread(source_status, source_path())
        except (OSError, TeamSourceError):
            if not requested:
                version.set_text("연결 확인 필요")
            return
        if not requested and not updates.syncing and generation == updates.generation:
            updates.observed(info["commit"])
        show_version()

    async def update():
        nonlocal requested, busy
        if busy:
            return
        requested = busy = True
        button.disable()
        show_status("busy", "팀 GitHub 확인 중…")
        try:
            info = await updates.sync()
            if info["changed"]:
                show_status("updated", "새 팀 코드를 받았어요.")
            else:
                show_status("current", "이미 최신 버전이에요.")
        except (OSError, TeamSourceError) as exc:
            show_status("error", f"갱신 실패 · {exc}")
        finally:
            busy = False
            show_version()

    with ui.element("div").classes("uav-team-code").props('data-testid=team-source-panel'):
        button = ui.button("팀 코드 갱신", on_click=update).props(
            'outline no-caps dense color=white data-testid=team-source-sync')
        button.tooltip("팀 GitHub의 최신 시뮬레이터 코드를 이 PC에 받습니다.")
        version = ui.label().classes("uav-source-version").props("data-testid=team-source-version")
        status = ui.label().classes("uav-source-status").props(
            "role=status aria-live=polite data-testid=team-source-status data-state=ready")
    updates.subscribe(show_version)
    show_version()
    ui.timer(0.05, load_version, once=True)
