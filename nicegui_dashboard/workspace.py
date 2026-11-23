"""NiceGUI application. Each page owns its library, renderer and selection."""
from __future__ import annotations

import asyncio

from fastapi.responses import RedirectResponse
from nicegui import app, ui

from .library_view import render_library
from .package_web import PackageMedia, register_package_routes
from .registry_notice import render_registry_notice
from .team_region_view import RegionalRuns
from .team_source_view import TeamCodeUpdates, render_team_code


SOURCES = {"S": ("상암 DMC", "/sangam"), "B": ("전체 지역", "/regions"), "L": ("이전 실행 기록", "/local")}
TABS = {"map": "지도 · 사건", "compare": "실험 비교", "performance": "현재 모델"}

SHELL_CSS = """
.nicegui-content {padding:0; gap:0; width:100%; max-width:none}
.uav-top {width:100%; background:#122c3b; color:white; display:flex; align-items:center; flex-wrap:wrap;
 padding:10px clamp(12px,2vw,32px); gap:16px; font-family:system-ui,sans-serif}
.uav-top .uav-title {font-size:19px; font-weight:700; letter-spacing:-.04em; margin-right:12px}
.uav-top a {color:#dbe8ef; text-decoration:none; border-radius:5px; padding:7px 11px; white-space:nowrap}
.uav-top a[aria-current=page] {background:#e0f3f0;color:#0d4f50;font-weight:700}
.uav-top .uav-download {margin-left:auto; border:1px solid #617b87;font-size:13px}
.uav-top .uav-history {font-size:13px}
.uav-team-code {display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-left:auto;max-width:100%}
.uav-team-code .q-btn {padding:3px 10px}
.uav-source-version,.uav-source-status {font-size:13px;color:#dbe8ef}
.uav-source-status {max-width:45ch;overflow-wrap:anywhere}
.uav-source-status:empty {display:none}
.uav-source-status[data-state=error] {color:#ffd4cc}
.uav-team-code ~ .uav-download {margin-left:0}
.uav-layout {display:grid;grid-template-columns:minmax(240px,285px) minmax(0,1fr);gap:16px;
 align-items:start; padding:16px; width:100%; box-sizing:border-box;background:#f0f4f6}
.uav-layout > * {min-width:0}
.uav-library {background:white; border:1px solid #d0dce2; padding:16px; border-radius:10px;}
.uav-library .uav-region-card {flex:none; height:auto; border:1px solid; border-radius:7px; padding:9px 12px}
.uav-region-card .q-btn__content {min-width:0; text-align:left; white-space:normal; word-break:keep-all; overflow-wrap:anywhere}
.uav-layout:has(.uav-library > .q-expansion-item--collapsed) {grid-template-columns:minmax(0,1fr)}
.uav-library:has(> .q-expansion-item--collapsed) {padding:4px 12px}
.uav-main {min-width:0; width:100%; gap:8px}
.uav-nav {background:white; border-radius:8px; padding:4px 8px; width:100%}
.uav-nav button[aria-pressed=true] {color:#006d68; background:#e0f3f0}
.uav-local {width:100%; max-width:1500px; margin:0 auto; padding:20px}
@media (max-width:1000px) {.uav-layout{grid-template-columns:minmax(0,1fr)} .uav-library .q-expansion-item__content [data-testid=library-cards]{max-height:30vh!important}}
@media (max-width:600px) {.uav-layout{padding:6px;gap:8px}.uav-library{padding:8px}.uav-top{gap:4px}.uav-top strong{width:100%}.uav-top a{padding:6px}.uav-top .uav-download{margin-left:0}}
"""


def header(source, team_updates=None):
    ui.add_css(SHELL_CSS)
    with ui.element("header").classes("uav-top"):
        ui.label("UAV 통신 대시보드").classes("uav-title")
        regional = ui.link(*SOURCES["B"]).props("data-source=B")
        if source == "B":
            regional.props("aria-current=page")
        if source == "B" and team_updates is not None:
            render_team_code(team_updates)
        if source in ("S", "B"):
            ui.link("기존 비행 공유본 받기", f"/part2/data/{source}/download").classes("uav-download").props("download")
        history = ui.link(*SOURCES["L"]).classes("uav-history").props("data-source=L")
        if source == "L":
            history.props("aria-current=page")


def serve_workspace(args, digests):
    from .replay_view import render_replay
    from .team_source import TeamSourceError, source_path, source_status
    from .team_update import TeamUpdates
    from .local_view import install_assets

    team_updates = TeamCodeUpdates()
    runs = TeamUpdates(args.data_root, max_output_gib=args.max_output_gib) if args.data_root else None
    install_assets()

    @app.get("/api/team-source", include_in_schema=False)
    def connected_team_source():
        try:
            info = source_status(source_path())
        except (OSError, TeamSourceError) as exc:
            return {"connected": False, "message": str(exc)}
        return {"connected": True, "repository": info["repository"], "branch": info["branch"], "commit": info["commit"]}

    media = PackageMedia({"S": args.s, "B": args.b}, args.codec_dir, digests)
    register_package_routes(app, media)
    app.on_shutdown(media.close)

    async def playback_page(source):
        header(source, team_updates)
        render_registry_notice(args.service_url)
        try:
            package = await asyncio.to_thread(media.package, source)
        except FileNotFoundError as exc:
            ui.label(f"{SOURCES[source][0]} 자료를 찾을 수 없습니다. {exc.filename}").props("role=alert").classes("p-5")
            if not media.paths[source].is_file():
                ui.label(f"공유 HTML 위치를 UAV_SHARED_{source}에 지정한 뒤 대시보드를 다시 실행하세요.")
            return
        except (OSError, ValueError, KeyError) as exc:
            ui.label(f"{SOURCES[source][0]} 자료를 열 수 없습니다. {exc}").props("role=alert").classes("p-5")
            return
        with ui.element("div").classes("uav-layout"):
            team = None
            async def select_flight(rid, revision):
                package.entry(rid, revision)
                if team:
                    await team.select((rid, revision), restart=False)
                else:
                    await replay.select_run(rid, revision)

            async def open_flight(rid, revision):
                package.entry(rid, revision)
                if team:
                    await team.select((rid, revision), restart=True)
                else:
                    await replay.open_run(rid, revision)

            with ui.element("aside").classes("uav-library"):
                with ui.expansion("지도 목록", value=source == "B").classes("w-full").props("dense"):
                    library = render_library(package.catalog, source=source, on_select=select_flight, on_open=open_flight)

            nav = {}
            def update_nav(tab):
                for key, button in nav.items():
                    button.props("aria-pressed=" + str(tab == key).lower())

            def on_state(state):
                rid, revision = state.get("run_id"), state.get("revision")
                if rid in package.catalog["entries"] and revision == package.catalog["entries"][rid]["revision"]:
                    if team:
                        team.archive_state((rid, revision))
                    else:
                        library.sync_selection(rid, revision)
                if state.get("tab") in TABS:
                    update_nav(state["tab"])

            def select_tab(key):
                async def activate():
                    await replay.set_tab(key)
                return activate

            with ui.column().classes("uav-main"):
                if library.initial_run is None:
                    ui.label("이 자료에는 재생 가능한 비행이 없습니다.").props("role=status")
                    return
                if source == "B":
                    team = RegionalRuns(runs, library, args.service_url, team_updates)
                    team.render()
                with ui.column().classes("w-full gap-2") as archive:
                    with ui.row().classes("uav-nav items-center gap-1").props('role=navigation aria-label="분석 화면"'):
                        for key, label in TABS.items():
                            nav[key] = ui.button(label, on_click=select_tab(key)).props(f"flat no-caps data-uav-tab={key}")
                    update_nav("map")
                    rid, revision = library.initial_run
                    replay = render_replay(source, on_state=on_state, initial_run=rid, initial_revision=revision)
                if team:
                    team.attach(archive, replay)

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse(SOURCES["B"][1])

    @ui.page(SOURCES["S"][1], title="상암 DMC · UAV 통신 대시보드", response_timeout=30)
    async def sangam():
        await playback_page("S")

    @ui.page(SOURCES["B"][1], title="전체 지역 · UAV 통신 대시보드", response_timeout=30)
    async def regions():
        await playback_page("B")

    @ui.page(SOURCES["L"][1], title="로컬 결과 · UAV 통신 대시보드")
    def local():
        from .local_view import render_local
        header("L")
        with ui.column().classes("uav-local"):
            render_local(base_url=args.service_url, data_root=args.data_root)

    ui.run(host="127.0.0.1", port=args.port, show=False, reload=False, title="UAV 통신 대시보드",
           show_welcome_message=False, language="ko-KR")
