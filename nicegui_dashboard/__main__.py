"""Start the UAV NiceGUI workspace or inspect the original data sources."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from .managed_service import SERVICE_PROJECT
from .portable import PortableDashboard
from .service import ServiceReader


PROJECT = Path(__file__).resolve().parents[1]
DATA = PROJECT / "data"
CODECS = Path(__file__).parent / "vendor" / "codecs"
SOURCES = {"S": "상암 최신 공유본", "B": "전체 지역 V2", "L": "로컬 결과 서비스"}
BASELINE = {
    "S": "f9a6d575b20a1ba0c0b389b0a50b2f4a013214aff2700b2a2c35c6bc888061fa",
    "B": "8ff2a0f512a0592cfeacf157db2eef05ecedc8d4f3bbf635ff9c87873b0d23e6",
}


def shared_summary(path, codec_dir, source, region=None):
    with PortableDashboard(path, codec_dir=codec_dir) as store:
        digest = store.file_digest()
        if digest != BASELINE[source]:
            raise ValueError(f"{path.name}의 SHA-256 ({digest})이 {source} 파트 1 기준 파일과 다릅니다.")
        catalog = store.catalog
        regions = catalog["scope"]["maps"]
        summary = {"format": catalog["format"], "maps": len(regions),
                   "runs": len(catalog["entries"]), "regions": regions, "file_sha256": digest}
        if region is None:
            return summary
        if region not in regions:
            raise ValueError(f"선택한 파일에 지역 {region!r}이 없습니다.")
        summary["region"] = region
        flights = []
        for mode, run_id in catalog["scope"]["flight_pairs"][region].items():
            revision = catalog["entries"][run_id]["revision"]
            entry = store.entry(run_id, revision)
            manifest = entry["manifest"]
            events = store.read_chunk(entry["events_chunk"])
            flights.append({
                "mode": mode, "run_id": run_id, "revision": revision,
                "map_hash": manifest["map_hash"], "duration_s": manifest["simulation_end_s"],
                "schema_version": manifest["schema_version"], "exporter_version": manifest["exporter_version"],
                "clocks": manifest["clocks"], "events": len(events["events"]),
                "pdr": manifest["final_metrics"].get("pdr"),
                "persistent_onsets": manifest["final_metrics"].get("persistent_onsets"),
                "persistent_cut_seconds": manifest["final_metrics"].get("persistent_cut_seconds"),
                "regional_margin_available": store.margin(run_id, revision) is not None,
            })
        summary["flights"] = flights
        evaluation = catalog.get("dashboard_analysis", {}).get("evaluations", {}).get(region)
        summary["reference_evaluation"] = evaluation and {
            key: evaluation.get(key) for key in ("evaluation_scope", "reference_controller", "evaluated_model", "summary")
        }
        return summary


def read_source(args, source):
    if source == "L":
        reader = ServiceReader(args.service_url, timeout=4)
        health = reader.health()
        rows = reader.runs()["runs"]
        return {"health": health, "registry_rows": len(rows), "jobs": reader.jobs()["capabilities"],
                "example_records": [{key: row.get(key) for key in ("run_id", "bundle_revision", "artifact_kind")}
                                    for row in rows[:3]]}
    path = args.s if source == "S" else args.b
    region = args.region if args.region is not None else ("sangam_dmc" if source == "S" else None)
    return shared_summary(path, args.codec_dir, source, region)


def serve(args):
    from nicegui import ui

    @ui.page("/")
    def page():
        with ui.column().classes("w-full max-w-4xl mx-auto p-5 gap-4"):
            ui.label("대시보드 자료 연결 확인").classes("text-2xl font-bold")
            ui.label("공유 파일의 비행 기록과 로컬 서비스의 등록 목록을 읽습니다.")
            with ui.row().classes("items-center gap-3"):
                source = ui.select(SOURCES, value=args.source, label="자료 출처").classes("w-60")
                button = ui.button("읽기")
            status = ui.label().props('role=status')
            panel = ui.column().classes("w-full")

            async def load():
                selected = source.value
                button.disable()
                source.disable()
                panel.clear()
                status.set_text(f"{SOURCES[selected]} 읽는 중…")
                try:
                    result = await asyncio.to_thread(read_source, args, selected)
                    if selected == "L":
                        status.set_text(f"서비스 {result['health']['service_version']} · 등록 목록 {result['registry_rows']}건")
                    else:
                        status.set_text(f"지도 {result['maps']}개 · 비행 {result['runs']}건")
                    with panel:
                        for flight in result.get("flights", []):
                            label = {"nominal": "기본 대형", "v21": "예측·재배치 적용"}.get(flight["mode"], flight["mode"])
                            ui.label(f"{label} · {flight['duration_s']:,g}초 · 사건 목록 {flight['events']}건")
                        with ui.expansion("원본 식별자와 조회 결과").classes("w-full"):
                            ui.code(json.dumps(result, ensure_ascii=False, indent=2)).classes("w-full overflow-auto")
                except (OSError, EOFError, ValueError, KeyError, RuntimeError) as exc:
                    status.set_text(f"{SOURCES[selected]} 읽기 실패 · {exc}")
                finally:
                    source.enable()
                    button.enable()

            button.on_click(load)
            def change_source():
                panel.clear()
                status.set_text(f"{SOURCES[source.value]} · 읽기를 누르면 선택한 자료를 조회합니다.")

            source.on_value_change(change_source)
            ui.timer(0.1, load, once=True)

    ui.run(host="127.0.0.1", port=args.port, show=False, reload=False, title="대시보드 자료 연결 확인")


def load_path_settings(path):
    if not path.is_file():
        return
    settings = json.loads(path.read_text(encoding="utf-8-sig"))
    allowed = {"UAV_SHARED_S", "UAV_SHARED_B", "UAV_DATA_ROOT", "UAV_SERVICE_PROJECT", "UAV_RESEARCH_CORE"}
    if not isinstance(settings, dict) or settings.keys() - allowed:
        raise ValueError("settings.local.json contains an unrecognized path option")
    for key, value in settings.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"settings.local.json needs a path for {key}")
        if not os.environ.get(key):
            os.environ[key] = str((path.parent / value).resolve())


def main():
    load_path_settings(PROJECT / "settings.local.json")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("probe", "serve", "app"))
    parser.add_argument("--source", choices=SOURCES, default="S")
    parser.add_argument("--s", type=Path, default=os.getenv("UAV_SHARED_S") or DATA / "상암DMC_대시보드.html")
    parser.add_argument("--b", type=Path, default=os.getenv("UAV_SHARED_B") or DATA / "UAV_대시보드_v2.html")
    parser.add_argument("--codec-dir", type=Path, default=os.getenv("UAV_CODEC_DIR") or CODECS)
    connection = parser.add_mutually_exclusive_group()
    connection.add_argument("--service-url", help="이미 실행 중인 로컬 결과 서비스의 주소")
    connection.add_argument("--data-root", type=Path,
                            help="app과 함께 실행할 서비스의 결과 폴더. 기본값은 data/results")
    parser.add_argument("--max-output-gib", type=float, default=4.0,
                        help="--data-root로 여는 기존 서비스의 용량 설정(GiB)")
    parser.add_argument("--region", help="지도 ID. 생략하면 S는 상암 두 비행, B는 카탈로그를 조회합니다.")
    parser.add_argument("--port", type=int, help="대시보드 기본 포트 8092, 자료 연결 확인 기본 포트 8091")
    args = parser.parse_args()
    if args.data_root and args.command != "app":
        parser.error("--data-root는 app 실행에 사용하세요.")
    if args.service_url is None and args.data_root is None:
        args.service_url = os.getenv("UAV_SERVICE_URL")
        if args.command == "app" and not args.service_url:
            args.data_root = Path(os.getenv("UAV_DATA_ROOT") or DATA / "results")
    if args.command != "app" and not args.service_url:
        args.service_url = "http://127.0.0.1:8765"
    if args.port is None:
        args.port = 8092 if args.command == "app" else 8091
    if args.command == "app":
        from .workspace import serve_workspace
        if args.data_root:
            from .managed_service import serve_results
            with serve_results(Path(os.getenv("UAV_SERVICE_PROJECT") or SERVICE_PROJECT), args.data_root,
                               quota_gib=args.max_output_gib) as args.service_url:
                print(f"Local results: {args.data_root.resolve()} ({args.service_url})", flush=True)
                serve_workspace(args, BASELINE)
        else:
            serve_workspace(args, BASELINE)
    elif args.command == "serve":
        serve(args)
    else:
        try:
            print(json.dumps(read_source(args, args.source), indent=2, ensure_ascii=False))
        except (OSError, EOFError, ValueError, KeyError, RuntimeError) as exc:
            parser.exit(1, f"{SOURCES[args.source]} 읽기 실패 · {exc}\n")


if __name__ == "__main__":
    main()
