"""Run the team's installed half-second release and record its actual outputs.

Invoked in an isolated simulator interpreter, never in the NiceGUI server.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from functools import partial
from unittest.mock import patch

from .team_demo import demo_survey, demo_survey_supported, needs_demo, resolve_demo_scene, study_roles


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_result(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".capture-", delete=False) as stream:
        tmp = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class FlightMeasurements:
    """Tap successful beacon receptions and native counters without consuming RNG."""

    def __init__(self):
        self.beacons = []
        self.counters = []
        self.frame = 0
        self.last_report = time.monotonic()

    def watch_sensor(self, sensor):
        import numpy as np

        transmit = sensor.transmit

        def transmit_and_record(frame, now, *args):
            slots = transmit(frame, now, *args)
            if slots:
                source, dest = np.nonzero(sensor.stamp == now)
                self.beacons.extend((now, int(a), int(b), float(sensor.incoming[a, b]),
                                     float(sensor.sinr[a, b])) for a, b in zip(source, dest))
            return slots

        sensor.transmit = transmit_and_record
        return sensor

    def watch_packets(self, packet):
        interval = packet.interval

        def interval_and_record(*args):
            result = interval(*args)
            self.frame += 1
            if self.frame % 5 == 0:
                self.counters.append((int(packet.ic[0]), int(packet.ic[1]), int(packet.ic[8])))
            if self.frame % 600 == 0 and time.monotonic() - self.last_report >= 20:
                print(json.dumps({"stage": "simulating", "simulation_time_s": self.frame / 10}),
                      flush=True)
                self.last_report = time.monotonic()
            return result

        packet.interval = interval_and_record
        return packet


def capture(code, core, spec, commit, max_seconds=None):
    prepared = json.loads((code.parent / "snapshot.json").read_text(encoding="utf-8"))
    if prepared.get("sha") != commit or Path(prepared.get("release_root", "")).resolve() != core.resolve():
        raise ValueError("준비된 팀 커밋·연구 자료가 선택한 실행과 일치하지 않습니다.")
    import numpy as np

    # Use the launcher shipped in the runtime to resolve its registered data.
    # The standalone UI has no torch, checkpoint or research module dependency.
    old_cwd = Path.cwd()
    sys.path.insert(0, str(code))
    os.chdir(code)
    try:
        from run_latest_final_v1 import activate_latest
        activate_latest()
        from adaptive_joint_expanded.features import OFFSETS
        from fast_halfsec import context, runner
        from final_v1 import fly
        from final_v1.halfsec_main import DECISION_S, MODE, release
        from swarm_sim.mission import MAX_MISSION
        if not Path(release.__file__).resolve().is_relative_to(code):
            raise RuntimeError("팀 시뮬레이터가 준비한 코드 폴더에서 로드되지 않았습니다.")
        if DECISION_S != .5 or not math.isclose(runner.DT, .1):
            raise ValueError("팀 기록 주기가 바뀌었습니다. 재생 기록 형식을 확인하세요.")
        if max_seconds is not None and (not math.isfinite(max_seconds) or
                not 0 < max_seconds <= MAX_MISSION or not float(max_seconds * 2).is_integer()):
            raise ValueError("진단 시간은 0.5초의 배수이고 팀 임무 제한 이하여야 합니다.")

        probes = FlightMeasurements()
        context_data = {}
        team_flight = release.MainFlight
        catalog = json.loads((core / "dashboard_execution/selected_maps.json").read_text(encoding="utf-8"))
        selected = [card for card in catalog["maps"] if card["key"] == spec["region"]]
        if len(selected) != 1:
            raise ValueError("시연할 지도 한 곳을 지역 카드에서 확인할 수 없습니다.")
        card = selected[0]
        context_source = Path(context.__file__).read_text(encoding="utf-8-sig")
        roles = study_roles(context_source)

        class RecordedFlight(team_flight):
            def survey(self, region, direction):
                # The team release may switch between two independently hashed registries.
                entry = context.registry().get(region)
                if region != card["key"] or (entry is not None and entry["sha256"] != card["sha256"]):
                    raise ValueError("기존 지역 카드와 팀 실행의 지도가 다릅니다.")
                if needs_demo(entry, roles):
                    if not demo_survey_supported(context_source) or self.survey_factory is not context.survey_for:
                        raise ValueError("팀의 지도 준비 방식이 변경되어 시연 지도 연결을 다시 검증해야 합니다.")
                    scene = resolve_demo_scene(core, card)
                    factory = partial(demo_survey, context, card, scene)
                    with patch.object(self, "survey_factory", factory):
                        data = super().survey(region, direction)
                    source, split = "dashboard_demonstration", "external_demonstration"
                else:
                    scene = (context.LEGACY / entry["path"]).resolve()
                    if not scene.is_file() or digest(scene) != entry["sha256"]:
                        raise ValueError(f"팀 지도 {region}의 파일 또는 체크섬을 확인하세요.")
                    data = super().survey(region, direction)
                    source, split = "team_registry", entry["study_role"]
                context_data.update(scene=scene, map_sha=card["sha256"], split=split,
                                    map_input_source=source, source_study_role=entry.get("study_role") if entry else None,
                                    altitude=float(data[1].altitude_m))
                return data

            def navigator(self, *args):
                nav = super().navigator(*args)
                context_data["nav"] = nav
                return nav

            def sensors(self, *args):
                return probes.watch_sensor(super().sensors(*args))

            def packet(self, *args, **kwargs):
                return probes.watch_packets(super().packet(*args, **kwargs))

        # Dispatch through the team's actual default. A changed, unsupported
        # default must not silently display an older model under a new commit.
        native_run = release.run
        parse_args = argparse.ArgumentParser.parse_args
        captured = []

        def verify_controller(parser, *args, **kwargs):
            parsed = parse_args(parser, *args, **kwargs)
            if getattr(parsed, "controller", None) != MODE:
                raise ValueError("새 팀 버전의 기본 제어기가 변경되어 기록 형식을 확인해야 합니다.")
            return parsed

        def record_native_run(*args, **kwargs):
            result = native_run(*args, **kwargs)
            captured.append(result)
            return result

        argv = ["fly", "--region", spec["region"], "--seed", str(spec["seed"]),
                "--kind", spec["kind"], "--direction", spec["direction"]]
        if max_seconds is not None:
            argv.extend(["--steps", str(int(max_seconds / DECISION_S))])
        started = utc()
        with (patch.object(release, "MainFlight", RecordedFlight), patch.object(sys, "argv", argv),
              patch.object(argparse.ArgumentParser, "parse_args", verify_controller),
              patch.object(release, "run", record_native_run)):
            fly.main()
        completed = utc()
        if len(captured) != 1:
            raise ValueError("팀 실행 경로가 변경되어 한 비행의 기록을 식별할 수 없습니다.")
        row, frames = captured[0]
        count = len(frames["network"])
        if (count != len(probes.counters) or not math.isclose(row["duration_s"], .5 * count)
                or probes.frame != 5 * count or row["plan_ticks"] != count
                or row["decision_interval_s"] != DECISION_S or row["controller"] != MODE
                or not row["safety_passed"] or not row["packet_conserved"]):
            raise ValueError("팀 비행 기록 시각, 제어기 또는 물리·패킷 검사 결과가 일치하지 않습니다.")
        if len(frames["plan_frames"]) != len(frames["scores"]) or len(frames["support_counts"]) != len(frames["scores"]):
            raise ValueError("팀 예측 시각과 점수 개수가 다릅니다.")
        final, _ = context_data["nav"].motion.positions()
        archive = {k:frames[k] for k in ("network", "links", "positions", "margin_db", "applied_offsets",
                                          "plan_frames", "scores", "support_counts")}
        archive.update(final_positions=np.array(final, copy=True), offset_xy_m=np.array(OFFSETS, copy=True),
                       packet_counters=np.asarray(probes.counters, dtype=np.int64),
                       beacon_rx=np.asarray(probes.beacons, dtype=float).reshape(-1, 5))
        release_meta = json.loads(release.MANIFEST.read_text(encoding="utf-8"))
        row.update(team_commit=commit, started_at=started, completed_at=completed,
                   full_mission_budget_s=MAX_MISSION, max_seconds_requested=max_seconds,
                   altitude_m=context_data["altitude"], split=context_data["split"],
                   map_input_source=context_data["map_input_source"], source_study_role=context_data["source_study_role"],
                   map_sha256=context_data["map_sha"], team_capture_version="1",
                   model_sha256=release_meta["sha256"], model_threshold=release_meta["threshold"])
        return row, archive, context_data["scene"]
    finally:
        os.chdir(old_cwd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("core", "code", "output-root", "result"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--seed", type=int, default=2026110701)
    parser.add_argument("--kind", choices=("continuous", "intermittent", "clear"), default="continuous")
    parser.add_argument("--direction", choices=("east_west", "north_south"), default="east_west")
    parser.add_argument("--max-seconds", type=float, help="Short, unpublished diagnostics only")
    parser.add_argument("--capture-dir", type=Path, help="Optional retained native recording")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.region) or not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        parser.error("Invalid region or commit")
    if args.max_seconds is not None and args.capture_dir is None:
        parser.error("--max-seconds requires --capture-dir; shortened diagnostics cannot be published")
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    spec = dict(region=args.region, seed=args.seed, kind=args.kind, direction=args.direction)
    print(json.dumps(dict(stage="flight_start", **spec, team_commit=args.commit)), flush=True)
    row, arrays, scene = capture(args.code.resolve(), args.core.resolve(), spec, args.commit, args.max_seconds)
    if args.capture_dir:
        import numpy as np
        args.capture_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(args.capture_dir / "frames.npz", **arrays)
        write_result(args.capture_dir / "flight.json", row)
        write_result(args.capture_dir / "source.json", {"scene": str(scene)})
    if args.max_seconds is not None:
        write_result(args.result, dict(diagnostic=True, duration_s=row["duration_s"], team_commit=args.commit))
        return
    from .team_bundle import publish_capture
    pin = publish_capture(row, arrays, scene, args.output_root.resolve())
    write_result(args.result, dict(v21=pin, team_commit=args.commit, region=args.region))


if __name__ == "__main__":
    main()
