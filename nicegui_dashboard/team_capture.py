"""Run the team's installed half-second release and record its actual outputs.

Invoked in an isolated simulator interpreter, never in the NiceGUI server.
"""
import argparse
from contextlib import nullcontext
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
from .team_telemetry import NativeChannels


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
        self.forecast_frames = []
        self.channels = NativeChannels()
        self.onset_probabilities = []
        self.frame = 0
        self.last_report = time.monotonic()

    def observe_fleet_head(self, module, inputs, logits):
        # Inspect the exact forward pass used by JointPolicy.forecast. Returning
        # None from a PyTorch hook leaves the logits passed to the controller intact.
        if tuple(logits.shape) != (1, 4) or self.frame % 5:
            raise ValueError("편대 단절 예측의 분류 출력 또는 판단 시각이 달라졌습니다.")
        probability = logits.detach().softmax(dim=-1)[0, 2:].sum().item()
        # A sum of float32 softmax masses can round one ULP above unity.
        if not math.isfinite(probability) or not 0 <= probability <= 1 + 2**-23:
            raise ValueError("편대 단절 예측 출력을 확인할 수 없습니다.")
        self.forecast_frames.append(self.frame // 5)
        self.onset_probabilities.append(min(probability, 1.))

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
            if hasattr(packet, "attempts"):
                self.channels.packets(packet, self.frame)
            if self.frame % 5 == 0:
                self.counters.append((int(packet.ic[0]), int(packet.ic[1]), int(packet.ic[8])))
            if self.frame % 600 == 0 and time.monotonic() - self.last_report >= 20:
                print(json.dumps({"stage": "simulating", "simulation_time_s": self.frame / 10}),
                      flush=True)
                self.last_report = time.monotonic()
            return result

        packet.interval = interval_and_record
        return packet


def capture(code, core, spec, commit, max_seconds=None, *, nominal=False, include_evaluation=True):
    from .team_runtime import native_path
    code = native_path(code)
    prepared = json.loads((code.parent / "snapshot.json").read_text(encoding="utf-8"))
    if prepared.get("sha") != commit or Path(prepared.get("release_root", "")).resolve() != core.resolve():
        raise ValueError("준비된 팀 커밋·연구 자료가 선택한 실행과 일치하지 않습니다.")
    import numpy as np
    if nominal:
        from .team_baseline import NominalFormation

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
        from .team_entry import planned_map, selected_entry
        entrypoint, flight_class, controller = selected_entry(fly, release, MODE)
        planned = entrypoint is not release
        team_flight = getattr(entrypoint, flight_class)
        catalog = json.loads((core / "dashboard_execution/selected_maps.json").read_text(encoding="utf-8"))
        selected = [card for card in catalog["maps"] if card["key"] == spec["region"]]
        if len(selected) != 1:
            raise ValueError("시연할 지도 한 곳을 지역 카드에서 확인할 수 없습니다.")
        card = selected[0]
        context_source = Path(context.__file__).read_text(encoding="utf-8-sig")
        roles = study_roles(context_source)

        class RecordedFlight(team_flight):
            def run(self, *args, **kwargs):
                with (self.model.fleet.register_forward_hook(probes.observe_fleet_head),
                      probes.channels.observe(runner,self.model)):
                    return super().run(*args, **kwargs)

            def survey(self, region, direction):
                if planned:
                    data = super().survey(region, direction)
                    if data[0].sha256 != card["sha256"]:
                        raise ValueError("비행 전 배치와 재생 지도의 원본이 다릅니다.")
                    context_data["altitude"] = float(data[1].altitude_m)
                    return data
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
                packet = probes.watch_packets(super().packet(*args, **kwargs))
                context_data["packets"] = packet
                return packet

        flight_type = RecordedFlight
        if nominal:
            class RecordedNominal(NominalFormation, RecordedFlight):
                pass
            flight_type = RecordedNominal

        # Dispatch through the team's actual default. A changed, unsupported
        # default must not silently display an older model under a new commit.
        native_run = entrypoint.run
        parse_args = argparse.ArgumentParser.parse_args
        captured = []

        def verify_controller(parser, *args, **kwargs):
            parsed = parse_args(parser, *args, **kwargs)
            if getattr(parsed, "controller", None) != controller:
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
        scene_scope = planned_map(entrypoint, context, spec, core, card, nominal) if planned else nullcontext({})
        with (scene_scope as scene_info, patch.object(entrypoint, flight_class, flight_type), patch.object(sys, "argv", argv),
              patch.object(argparse.ArgumentParser, "parse_args", verify_controller),
              patch.object(entrypoint, "run", record_native_run)):
            context_data.update(scene_info)
            fly.main()
        completed = utc()
        if len(captured) != 1:
            raise ValueError("팀 실행 경로가 변경되어 한 비행의 기록을 식별할 수 없습니다.")
        row, frames = captured[0]
        count = len(frames["network"])
        if (count != len(probes.counters) or not math.isclose(row["duration_s"], .5 * count)
                or probes.frame != 5 * count or row["plan_ticks"] != count
                or row["decision_interval_s"] != DECISION_S or row["controller"] != controller
                or not row["safety_passed"] or not row["packet_conserved"]):
            raise ValueError("팀 비행 기록 시각, 제어기 또는 물리·패킷 검사 결과가 일치하지 않습니다.")
        if (len(frames["plan_frames"]) != len(frames["scores"]) or len(frames["support_counts"]) != len(frames["scores"])
                or not np.array_equal(probes.forecast_frames, frames["plan_frames"])):
            raise ValueError("팀 예측 시각과 점수 개수가 다릅니다.")
        final, _ = context_data["nav"].motion.positions()
        archive = {k:frames[k] for k in ("network", "links", "positions", "margin_db", "applied_offsets",
                                          "plan_frames", "scores", "support_counts")}
        if planned:
            archive["altitudes"] = frames["altitudes"]
            row["checkpoint_controller"] = MODE
        archive.update(final_positions=np.array(final, copy=True), offset_xy_m=np.array(OFFSETS, copy=True),
                       fleet_onset_probability=np.asarray(probes.onset_probabilities, dtype=np.float32),
                       packet_counters=np.asarray(probes.counters, dtype=np.int64),
                       beacon_rx=np.asarray(probes.beacons, dtype=float).reshape(-1, 5))
        packet = context_data["packets"]
        archive.update(probes.channels.arrays(frames, row["search_drones"], row["total_drones"]))
        if not packet.finished or packet.known.shape != (row["search_drones"], packet.visible.shape[1]):
            raise ValueError("팀 패킷 종료 정산과 수색기의 목표 셀 지식을 확인할 수 없습니다.")
        row.update(shared_cells=int(np.all(packet.known, axis=0).sum()), target_cells=packet.known.shape[1])
        if nominal:
            if row["events"] or row["applied_changes"] or np.any(archive["applied_offsets"]) or np.any(OFFSETS[0]):
                raise ValueError("기본 대형 비행에서 보정 명령 또는 적용된 보정 변위를 발견했습니다.")
            row["controller"] = "nominal_no_relocation"
        release_meta = json.loads(release.MANIFEST.read_text(encoding="utf-8"))
        row.update(team_commit=commit, started_at=started, completed_at=completed,
                   full_mission_budget_s=MAX_MISSION, max_seconds_requested=max_seconds,
                   altitude_m=context_data["altitude"], split=context_data["split"],
                   map_input_source=context_data["map_input_source"], source_study_role=context_data["source_study_role"],
                   map_sha256=context_data["map_sha"], team_capture_version="3",
                   model_sha256=release_meta["sha256"], model_threshold=release_meta["threshold"])
        from .team_analysis import native_diagnoses
        row["_incident_analysis"] = native_diagnoses(row, archive)
        if nominal and max_seconds is None and include_evaluation:
            from .team_evaluation import capture_reference
            row["_regional_evaluation"] = capture_reference(entrypoint, release, context, core, card, row,
                                                            context_data["scene"])
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
    # Bind both stages when this job starts. An application update during a
    # long flight must not change the comparison or publisher used by that job.
    from .team_bundle import publish_capture
    from .team_baseline import capture_pair
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
    baseline = capture_pair(args.code.resolve(), args.core.resolve(), row, arrays)
    pin = publish_capture(row, arrays, scene, args.output_root.resolve(), nominal=baseline)
    write_result(args.result, dict(v21=pin, team_commit=args.commit, region=args.region))


if __name__ == "__main__":
    main()
