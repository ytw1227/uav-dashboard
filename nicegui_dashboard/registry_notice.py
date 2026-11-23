"""Read-only notifications for fixed S/B presentation pages."""
import asyncio
from dataclasses import dataclass
from functools import lru_cache
from time import monotonic

from nicegui import ui

from .service import ServiceReader, ServiceReadError


@dataclass(frozen=True)
class Listing:
    generation: int
    pins: frozenset[tuple[str, str]]
    team_regions: dict[tuple[str, str], str]


class RegistryFeed:
    """One status check per period per service. Read the list only when needed."""

    def __init__(self, api):
        self.api = api
        self.current = None
        self.next_check = 0
        self.lock = asyncio.Lock()

    async def snapshot(self):
        async with self.lock:
            if monotonic() >= self.next_check:
                try:
                    status = await asyncio.to_thread(self.api.status)
                    if self.current is None or status["generation"] != self.current.generation:
                        catalog = await asyncio.to_thread(self.api.runs)
                        pins = frozenset((r["run_id"], r["bundle_revision"]) for r in catalog["runs"])
                        team_regions = {}
                        for row in catalog["runs"]:
                            metadata = row.get("metadata") or row
                            if metadata.get("adapter_id") == "team-halfsec" and metadata.get("region"):
                                team_regions[row["run_id"], row["bundle_revision"]] = metadata["region"]
                        self.current = Listing(catalog["generation"], pins, team_regions)
                except (ServiceReadError, KeyError, OSError, ValueError):
                    self.current = None
                self.next_check = monotonic() + 5
            return self.current


@lru_cache(maxsize=4)
def feed_for(base_url):
    return RegistryFeed(ServiceReader(base_url, timeout=3))


class PresentationNotice:
    """Only this tab owns its baseline and acknowledgement."""

    def __init__(self, feed, region_labels=None):
        self.feed = feed
        self.known = None
        self.unseen = set()
        self.region_labels = region_labels or {}
        self.team_regions = {}

    def render(self):
        with ui.row().classes("w-full items-center px-4 bg-white").props("data-testid=presentation-registry-notice"):
            self.label = ui.label().classes("text-caption").props('role=status')
            self.open_link = ui.link("로컬 결과 새 탭에서 열기", "/local?scope=all", new_tab=True)
            self.dismiss_button = ui.button("확인", on_click=self.dismiss).props("flat dense")
        self.update_text(None)
        ui.timer(0.1, self.refresh, once=True)
        ui.timer(5, self.refresh)

    async def refresh(self):
        snapshot = await self.feed.snapshot()
        if snapshot is not None:
            if self.known is not None:
                self.unseen.intersection_update(snapshot.pins)
                self.unseen.update(snapshot.pins - self.known)
            self.known = snapshot.pins
            self.team_regions = snapshot.team_regions
        self.update_text(snapshot is not None)

    def update_text(self, online):
        self.online = online
        state = {None: "pending", True: "online", False: "offline"}[online]
        self.label.props(f"data-registry-state={state}")
        regional = {pin: self.region_labels[self.team_regions[pin]] for pin in self.unseen
                    if self.team_regions.get(pin) in self.region_labels}
        other_count = len(self.unseen) - len(regional)
        parts = []
        if regional:
            names = sorted(set(regional.values()))
            locations = ", ".join(names[:3]) + (f" 외 {len(names)-3}곳" if len(names) > 3 else "")
            parts.append(f"새 비행 {len(regional)}건 · 지역 카드에서 보기 ({locations})")
        if other_count:
            parts.append(f"새 로컬 결과 또는 변환본 {other_count}건")
        message = " · ".join(parts)
        if online is False:
            message = " · ".join(filter(None, [message, "로컬 결과 서비스 연결 대기"]))
        self.label.set_text(message)
        self.open_link.set_visibility(bool(other_count))
        self.dismiss_button.set_visibility(bool(self.unseen))

    def dismiss(self):
        self.unseen.clear()
        self.update_text(self.online)


def render_registry_notice(base_url, region_labels=None):
    PresentationNotice(feed_for(base_url), region_labels).render()
