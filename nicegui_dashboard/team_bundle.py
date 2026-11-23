"""Adapt a recorded team inference flight to the local, immutable replay contract."""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import zlib

from .ingest import load_store, publish
from .managed_service import SERVICE_PROJECT


SCHEMA = """
CREATE TABLE positions(time_s REAL PRIMARY KEY, payload BLOB NOT NULL);
CREATE TABLE rf_frames(time_s REAL PRIMARY KEY, interval_start_s REAL, interval_end_s REAL, payload BLOB NOT NULL);
CREATE TABLE observations(time_s REAL, observer TEXT, peer TEXT, payload BLOB NOT NULL, PRIMARY KEY(time_s,observer,peer));
CREATE INDEX observations_pair_time ON observations(observer,peer,time_s);
CREATE TABLE decisions(time_s REAL PRIMARY KEY, payload BLOB NOT NULL);
CREATE TABLE events(event_id TEXT PRIMARY KEY, start_s REAL, end_s REAL, type TEXT, payload BLOB NOT NULL);
CREATE TABLE metrics(metric_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
"""


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def packed(value):
    return zlib.compress(encode(value), 1)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def make_scene(path, record):
    if digest(path) != record["map_sha256"]:
        raise ValueError("비행에 사용한 지도와 재생 지도의 해시가 다릅니다.")
    raw = json.loads(path.read_bytes())
    meta = raw["metadata"]
    buildings = []
    for building in raw["buildings"]:
        height = building.get("height_m")
        source = building.get("height_source")
        buildings.append(dict(
            id=str(building["building_id"]), geometry=building["geometry_local_m"],
            height_m=height, effective_height_m=height, height_source=source,
            height_status="estimated" if source == "imputed" else "recorded" if height is not None else "unknown",
            imputed=source == "imputed", observed_height_m=building.get("observed_height_m")))
    return dict(map_id=record["spec"]["region"], map_hash=record["map_sha256"],
                name=meta.get("region_name"), bounds=meta["local_bounds"], altitude_m=record["altitude_m"],
                coordinate_system=dict(kind="local_cartesian", unit="m", crs=meta.get("metric_crs"),
                                       origin=meta.get("local_origin_projected_m"), axes=meta.get("local_axes"),
                                       transform=meta.get("local_transform"), ground_z_m=meta.get("ground_z_m")),
                buildings=buildings, no_fly_zones=[dict(id=str(z.get("id", i)), geometry=z["geometry_local_m"])
                    for i, z in enumerate(raw.get("no_fly_zones", []))], attribution=meta.get("building_source"))


def components(links, ids):
    pending = set(range(len(ids)))
    groups = []
    while pending:
        node = min(pending)
        pending.remove(node)
        group, stack = {node}, [node]
        while stack:
            neighbours = {i for i in pending if links[stack[-1], i]}
            stack.pop()
            pending.difference_update(neighbours)
            group.update(neighbours)
            stack.extend(neighbours)
        groups.append([ids[i] for i in sorted(group)])
    return groups


METRICS = {
    "pdr": ("패킷 전달률 PDR", "ratio", "delivered / generated after packet drain"),
    "timely_pdr": ("0.25초 이내 전달률", "ratio", "native timely / max(1, generated)"),
    "ctr": ("수색망 연결 유지율 CTR", "ratio", "mean of recorded 0.5 s search connectivity"),
    "persistent_onsets": ("새 지속 단절 횟수", "count", "persistent intervals starting after t=0"),
    "persistent_cut_seconds": ("지속 단절 누적시간", "s", "sum of all outage intervals lasting at least 1 s"),
    "duration_s": ("비행 시간", "s", "native elapsed simulation time before packet drain"),
    "mission_completed": ("임무 완료", "boolean", "native completion condition"),
    "generated_packets": ("생성 패킷", "count", "native total generated packets"),
    "delivered_packets": ("전달 패킷", "count", "native total delivered packets after drain"),
}


def totals(record):
    return dict(pdr=record["delivered"] / record["generated"] if record["generated"] else None,
                timely_pdr=record["timely_pdr"], ctr=record["network_fraction"],
                persistent_onsets=record["onsets"], persistent_cut_seconds=record["cut_seconds"],
                duration_s=record["duration_s"], mission_completed=record["mission_completed"],
                generated_packets=record["generated"], delivered_packets=record["delivered"])


def validate_capture(row, arrays):
    import numpy as np

    if (not re.fullmatch(r"[0-9a-f]{40}", row["team_commit"]) or row["max_seconds_requested"] is not None
            or not row["safety_passed"] or not row["packet_conserved"] or row["decision_interval_s"] != .5):
        raise ValueError("정식 팀 실행의 버전·종료·검증 조건을 확인할 수 없습니다.")
    n, ns = row["total_drones"], row["search_drones"]
    steps = len(arrays["network"])
    if not 0 < ns < n or steps == 0 or not math.isclose(steps * .5, row["duration_s"]):
        raise ValueError("비행 시간 또는 기체 구성이 실제 기록과 다릅니다.")
    shapes = dict(positions=(steps, n, 2), final_positions=(n, 2), links=(steps, n, n),
                  margin_db=(steps,), network=(steps,), packet_counters=(steps, 3), applied_offsets=(steps, n-ns))
    for key, shape in shapes.items():
        if arrays[key].shape != shape or not np.isfinite(arrays[key]).all():
            raise ValueError(f"재생에 필요한 팀 기록의 형식이 다릅니다: {key}")
    if not np.array_equal(arrays["links"], arrays["links"].transpose(0, 2, 1)):
        raise ValueError("양방향 링크 기록을 확인할 수 없습니다.")
    offsets, applied = arrays["offset_xy_m"], arrays["applied_offsets"]
    if (offsets.ndim != 2 or offsets.shape[1] != 2 or not np.isfinite(offsets).all()
            or not np.issubdtype(applied.dtype, np.integer) or np.any((applied < 0) | (applied >= len(offsets)))):
        raise ValueError("기체별 보정 명령과 실제 적용 기록을 대조할 수 없습니다.")
    counters = arrays["packet_counters"]
    if (np.any(counters < 0) or np.any(np.diff(counters, axis=0) < 0)
            or np.any(counters[:, 1] > counters[:, 0]) or np.any(counters[:, 2] > counters[:, 1])
            or counters[-1, 0] != row["generated"] or counters[-1, 1] > row["delivered"]
            or counters[-1, 2] > row["timely"]
            or not 0 <= row["timely"] <= row["delivered"] <= row["generated"]
            or not math.isclose(row["timely_pdr"], row["timely"] / max(1, row["generated"]))):
        raise ValueError("팀 패킷 집계와 재생 중의 전달 기록이 일치하지 않습니다.")
    net = arrays["network"]
    boundaries = np.flatnonzero(np.diff(np.r_[True, net, True]))
    outages = [(float(start)*.5, float(end)*.5) for start, end in zip(boundaries[::2], boundaries[1::2])]
    cuts = [(a,b) for a,b in outages if b-a >= 1.]
    if (cuts != [tuple(c) for c in row["cuts"]] or row["onsets"] != sum(a>0 for a,b in cuts)
            or not math.isclose(sum(b-a for a,b in cuts),row["cut_seconds"])
            or not math.isclose(float(net.mean()), row["network_fraction"])):
        raise ValueError("팀 요약과 재생용 수색망 단절 기록이 일치하지 않습니다.")
    indices = arrays["plan_frames"]
    if not (len(indices) == len(arrays["scores"]) == len(arrays["support_counts"])
            and np.all((0 <= indices) & (indices < steps)) and np.all(np.diff(indices)>0)):
        raise ValueError("모델 판단 시각이 기록과 일치하지 않습니다.")
    probabilities = arrays.get("fleet_onset_probability")
    if row["team_capture_version"] == "2" or probabilities is not None:
        if (probabilities is None or probabilities.shape != indices.shape
                or not np.all(np.isfinite(probabilities) & (probabilities >= 0) & (probabilities <= 1))):
            raise ValueError("편대 단절 예측값 또는 판단 시각의 개수가 일치하지 않습니다.")
    return outages


def write_database(path, row, arrays, fleet, outages, definitions):
    ids = [u["id"] for u in fleet]
    ns = row["search_drones"]
    positions, last = arrays["positions"], arrays["final_positions"]
    score_at = {int(t): (float(s), int(n)) for t,s,n in
                zip(arrays["plan_frames"], arrays["scores"], arrays["support_counts"])}
    onset_at = dict(zip(arrays["plan_frames"], arrays.get("fleet_onset_probability", ())))
    requests = {e["time_s"]: e for e in row["events"]}
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(SCHEMA)
        def add_position(t, xy):
            uavs = [dict(u, x=float(point[0]), y=float(point[1]), z=row["altitude_m"]) for u,point in zip(fleet, xy)]
            db.execute("INSERT INTO positions VALUES(?,?)", (t, packed(dict(sample_time_s=t, available_at_s=t, uavs=uavs))))

        for k, xy in enumerate(positions):
            start, end = k*.5, (k+1)*.5
            add_position(start, xy)
            graph = arrays["links"][k]
            parts = components(graph, ids)
            reach = [[u for u in group if u in ids[:ns]] for group in parts]
            connected = any(len(group)==ns for group in reach)
            if connected != bool(arrays["network"][k]):
                raise ValueError(f"링크에서 읽은 수색망 연결 상태가 {start}초 원본 기록과 다릅니다.")
            gen, delivered, timely = map(int, arrays["packet_counters"][k])
            metrics = dict(search_connected=int(connected), network_margin_db=float(arrays["margin_db"][k]),
                           search_pair_reachability=sum(len(g)*(len(g)-1) for g in reach)/(ns*(ns-1)) if ns>1 else 1.,
                           generated_packets=gen, delivered_packets=delivered,
                           pdr_so_far=delivered/gen if gen else None, timely_pdr_so_far=timely/gen if gen else None)
            links = [dict(id=f"link-{i}-{j}", source=ids[i], target=ids[j], directed=False, available=bool(graph[i,j]))
                     for i in range(len(ids)) for j in range(i+1, len(ids))]
            frame = dict(interval_start_s=start, interval_end_s=end, available_at_s=end, graph_available_at_s=start,
                         links=links, components=parts, metrics=metrics)
            db.execute("INSERT INTO rf_frames VALUES(?,?,?,?)", (end, start, end, packed(frame)))
            nxt = positions[k+1] if k+1 < len(positions) else last
            request = requests.get(start)
            actions = []
            for i in range(ns,len(ids)):
                offset = int(arrays["applied_offsets"][k,i-ns])
                actions.append(dict(uav_id=ids[i], decision_time_s=start, interval_start_s=start, interval_end_s=end,
                                    applied_correction=arrays["offset_xy_m"][offset].tolist(),
                                    requested_correction=request["new_offset"] if request and request["changed_relay"]==i else None,
                                    actual_displacement=(nxt[i]-xy[i]).tolist(), actual_available_at_s=end, unit="m"))
            score, support = score_at.get(k, (None, 0))
            decision = dict(decision_time_s=start, available_at_s=start, actions=actions, predictions=[],
                            fleet_score=score if support>0 else None, support_count=support)
            if "fleet_onset_probability" in arrays:
                decision["fleet_onset_probability"] = float(onset_at[k]) if support > 0 else None
                decision["pre_action_connected"] = bool(arrays["network"][k-1]) if k else None
            db.execute("INSERT INTO decisions VALUES(?,?)", (start, packed(decision)))
        add_position(row["duration_s"], last)

        for t, sender, receiver, power, sinr in arrays["beacon_rx"]:
            observer, peer = ids[int(receiver)], ids[int(sender)]
            values = dict(beacon_sinr_db=float(sinr), beacon_rx_power_dbm=float(power))
            obs = dict(observed_at_s=t, available_at_s=t, observer_id=observer, peer_id=peer,
                       values=values, valid=dict.fromkeys(values,True), ages=dict.fromkeys(values,0.0))
            db.execute("INSERT INTO observations VALUES(?,?,?,?)", (float(t),observer,peer,packed(obs)))

        def event(typ, a, b, related, index, **detail):
            key = f"{typ}-{index:06d}"
            value = dict(id=key, type=typ, start_s=a, end_s=b, duration_s=b-a, definition_id=typ,
                         temporal_kind="interval" if a!=b else "instant", related_uav_ids=related, **detail)
            if a!=b:
                value.update(left_censored=a==0, right_censored=b==row["duration_s"])
            db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (key,a,b,typ,packed(value)))
        for i,(a,b) in enumerate(outages):
            event("search_outage",a,b,ids[:ns],i)
            if b-a>=1:
                event("persistent_search_outage",a,b,ids[:ns],i,counted_as_new_onset=a>0)
        for i,detail in enumerate(row["events"]):
            t = detail["time_s"]
            event("intervention",t,t,[ids[detail["changed_relay"]]],i,reason=detail["reason"],source_detail=detail)
        for i,(t, relay, offset, command_s) in enumerate(row["applied_changes"]):
            event("applied_command",t,t,[ids[relay]],i,offset_index=offset,
                  offset_xy_m=arrays["offset_xy_m"][offset].tolist(), source_command_s=command_s)
        for key, value in totals(row).items():
            metric_id = definitions[key]["id"]
            db.execute("INSERT INTO metrics VALUES(?,?)", (metric_id, encode(dict(
                metric_id=metric_id,key=key,value=value,definition=definitions[key])).decode()))
        return {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in ("positions","rf_frames","decisions","metrics","events","observations")}


def publish_capture(record, arrays, scene_path, output_root):
    quota = float(os.environ.get("UAV_RESULT_MAX_GIB", 4))
    if not math.isfinite(quota) or quota <= 0:
        raise ValueError("결과 보관소의 저장 용량 한도를 확인하세요.")
    outages = validate_capture(record, arrays)
    scene = make_scene(Path(scene_path), record)
    spec, sha = record["spec"], record["team_commit"]
    map_input = record.get("map_input_source", "team_registry")
    original_role = record.get("source_study_role", record["split"])
    if map_input not in ("team_registry", "dashboard_demonstration"):
        raise ValueError("비행에 사용한 지도의 입력 출처를 확인할 수 없습니다.")
    if map_input == "dashboard_demonstration" and (
            record["split"] != "external_demonstration" or "source_study_role" not in record):
        raise ValueError("시연 지도 결과의 구분 또는 원래 연구 용도가 기록되지 않았습니다.")
    identity = dict(spec=spec, team_commit=sha, map_hash=record["map_sha256"], model_sha256=record["model_sha256"],
                    started_at=record["started_at"], controller=record["controller"])
    fingerprint = hashlib.sha256(encode(identity)).hexdigest()
    revision_inputs = [fingerprint, record["team_capture_version"], record["completed_at"]]
    threshold = record.get("model_threshold")
    if type(threshold) in (int, float) and math.isfinite(threshold) and 0 <= threshold <= 1:
        revision_inputs.append({"model_threshold": threshold})
    else:
        threshold = None
    rid, rev = "team-" + fingerprint[:28], "rev-" + hashlib.sha256(encode(revision_inputs)).hexdigest()[:24]
    fleet = [dict(id=f"uav-{i}", role="search" if i<record["search_drones"] else "relay") for i in range(record["total_drones"])]
    defs = {}
    for key,(label,unit,formula) in METRICS.items():
        definition = dict(id="team-halfsec."+key,key=key,label=label,unit=unit,aggregation=formula,scope="run_total",
                          version="1",source="fast_halfsec/runner.py::full_mission",definition_status="verified")
        definition["definition_hash"] = hashlib.sha256(encode(definition)).hexdigest()
        defs[key] = definition
    with tempfile.TemporaryDirectory(prefix="uav-team-bundle-") as temp:
        bundle = Path(temp)/rid/rev
        bundle.mkdir(parents=True)
        (bundle/"scene.json").write_bytes(encode(scene))
        counts = write_database(bundle/"data.sqlite", record, arrays, fleet, outages, defs)
        artifacts = {name:dict(sha256=digest(bundle/name),size_bytes=(bundle/name).stat().st_size) for name in ("data.sqlite","scene.json")}
        manifest = dict(schema_version="1.0.0",run_id=rid,bundle_revision=rev,adapter_id="team-halfsec",adapter_version="1",
            artifact_kind="episode",research_family="final_v1_halfsec",source_key="source-"+fingerprint[:28],
            source_identity=identity,source_fingerprint=fingerprint,research_version=sha,team_commit=sha,
            source_refs=[dict(path="final_v1/halfsec_main/release.py",commit=sha)], stage_status="completed",export_status="ready",
            started_at=record["started_at"],completed_at=record["completed_at"],completion_time_status="recorded",
            model_id=record["controller"],model_sha256=record["model_sha256"],controller=record["controller"],mode="v21_halfsec",
            config_hash=hashlib.sha256(encode([spec,sha,record["model_sha256"]])).hexdigest(),
            region=spec["region"],seed=spec["seed"],kind=spec["kind"],direction=spec["direction"],split=record["split"],
            map_input_source=map_input,source_study_role=original_role,
            mission_completed=record["mission_completed"],map_id=spec["region"],map_hash=record["map_sha256"],scene_ref="scene.json",
            decision_interval_s=record["decision_interval_s"],simulation_end_s=record["duration_s"],fleet=fleet,
            clocks=dict(decision_dt_s=.5,rf_dt_s=.5,motion_dt_s=.1,position_sample_dt_s=.5),clock_definitions={
                "motion":"native physical integration at 0.1 s; saved observed XY at 0.5 s and mission end",
                "rf":"link state at interval start; cumulative packet counts at interval end before drain"},
            final_metrics=totals(record),metric_definitions=defs,observation_definitions={
                "beacon_sinr_db":dict(unit="dB",direction="peer to observer",source="successful beacon at reception"),
                "beacon_rx_power_dbm":dict(unit="dBm",direction="peer to observer",source="successful beacon at reception")},
            event_definitions=dict(search_outage="unreachable search pair, accounting for relay paths",
                persistent_search_outage="continuous outage of at least 1 s",intervention="native controller request",
                applied_command="changed command offset delivered to relay"), head_definitions={},
            capabilities={key:dict(status="available") for key in ("positions","scene","links","decisions","rf_observations","events","metrics")},
            validation_report=dict(counts=counts,safety_passed=record["safety_passed"],packet_conserved=record["packet_conserved"]),
            artifacts=artifacts)
        if threshold is not None:
            manifest["model_threshold"] = threshold
        if "fleet_onset_probability" in arrays:
            manifest["head_definitions"]["fleet_onset_probability"] = {
                "window_s": [3, 7], "window_bounds": "[)", "minimum_outage_s": 1,
                "scope": "next_persistent_search_network_outage",
                "source": "final_v1.halfsec_joint.model.JointPolicy.forecast/fleet_logits",
            }
        (bundle/"manifest.json").write_bytes(encode(manifest))
        (bundle/"READY").write_bytes(encode(dict(manifest_sha256=digest(bundle/"manifest.json"))))
        store = load_store(SERVICE_PROJECT, output_root, quota_gib=quota)
        try:
            from dashboard_contract.validation import validate_bundle
            validate_bundle(bundle)
            return publish(bundle, store)
        finally:
            store.close()
