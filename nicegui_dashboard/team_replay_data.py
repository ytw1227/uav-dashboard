"""Read a sealed team flight into the same display channels as the regional player."""
from contextlib import closing
from functools import lru_cache
import gzip
import io
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
import zlib

from fastapi import HTTPException, Query
from fastapi.responses import Response

from result_service.dashboard_contract.validation import confined, read_json, sha256, validate_manifest
from .package_web import compressed_json
from .team_forecast_record import FLEET_FORECAST_HEAD, forecast_path, load_forecast


ID = re.compile(r"[a-z0-9_-]{8,80}")
CHANNELS = ("positions", "rf_frames", "decisions")
RADIO_FIELDS = {"beacon_sinr_db": "beacon_received_sinr_db",
                "beacon_rx_power_dbm": "beacon_received_power_dbm"}


def database(root):
    return closing(sqlite3.connect((root / "data.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True))


def unpack(payload):
    return json.loads(raw_json(payload))


def raw_json(payload):
    # The dashboard contract permits either UTF-8 text or a compressed JSON row.
    return payload.encode("utf-8") if isinstance(payload, str) else zlib.decompress(payload)


def json_response(data):
    return zipped(compressed_json(data))


def zipped(raw):
    return Response(raw, media_type="application/json",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "private, no-cache"})


def load_events(root):
    from .team_incidents import timeline_events
    with database(root) as db:
        rows = [unpack(blob) for (blob,) in db.execute("SELECT payload FROM events ORDER BY start_s,event_id")]
        rows = timeline_events(db, rows)
    for row in rows:
        if row["type"] in {"persistent_search_outage", "search_outage"}:
            row["scope"] = "search_network"
        elif row["type"] == "applied_command":
            row["label"] = "보정 명령 적용"
    return rows


def display_manifest(manifest, events):
    cuts = [[e["start_s"], e["end_s"]] for e in events if e["type"] == "persistent_search_outage"]
    metrics = dict(manifest["final_metrics"])
    definitions = dict(manifest["metric_definitions"])
    metrics["search_connected_mean"] = metrics["ctr"]
    definitions["search_connected_mean"] = dict(definitions["ctr"], key="search_connected_mean")
    metrics["persistent_max_s"] = max((b - a for a, b in cuts), default=0.)
    definitions["persistent_max_s"] = dict(label="최장 지속 단절", key="persistent_max_s", unit="s",
                                            aggregation="longest recorded persistent search outage")
    fields = {RADIO_FIELDS.get(key, key): value for key, value in manifest["observation_definitions"].items()}
    return {**manifest, "recorded_mode": manifest["mode"], "mode": "nominal" if manifest["mode"] == "nominal" else "v21",
            "raw_metadata": {**manifest.get("raw_metadata", {}), "persistent_cut_intervals": cuts},
            "final_metrics": metrics, "metric_definitions": definitions, "observation_definitions": fields}


@lru_cache(maxsize=12)
def checked_record(root, signature):
    """Verify immutable artifacts once for this file signature, including data content."""
    ready = read_json(root / "READY", 4096)
    if ready.get("manifest_sha256") != sha256(root / "manifest.json"):
        raise ValueError("완료 기록과 비행 정보가 일치하지 않습니다.")
    m = validate_manifest(read_json(root / "manifest.json"))
    if ((m["run_id"], m["bundle_revision"]) != (root.parent.name, root.name)
            or m.get("adapter_id") != "team-halfsec"):
        raise ValueError("요청한 팀 비행과 기록의 식별자가 다릅니다.")
    for name in ("scene.json", "data.sqlite"):
        path = root / name
        meta = m["artifacts"][name]
        if path.stat().st_size != meta["size_bytes"] or sha256(path) != meta["sha256"]:
            raise ValueError("비행을 기록한 뒤 지도 또는 통신 자료가 달라졌습니다.")
    scene = read_json(root / "scene.json")
    if scene.get("map_hash") != m["map_hash"] or scene.get("map_id") != m["region"]:
        raise ValueError("비행에 사용한 지도와 재생 지도가 다릅니다.")
    events = load_events(root)
    return display_manifest(m, events), scene, events


def forecast_signature(root, manifest):
    if "fleet_onset_probability" in manifest.get("head_definitions", {}):
        return None
    try:
        path = forecast_path(root.parents[2], root.parent.name, root.name)
        s = path.stat()
        return s.st_ino, s.st_ctime_ns, s.st_mtime_ns, s.st_size
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=12)
def checked_forecast(root, signature, file_signature):
    if file_signature is None:
        return None
    m, _, _ = checked_record(root, signature)
    try:
        path = forecast_path(root.parents[2], m["run_id"], m["bundle_revision"])
        record = load_forecast(path, m, read_json(root / "READY", 4096)["manifest_sha256"])
        # The previous RF interval ends at the decision time. Check both the
        # support and the causal connection mask against the sealed flight.
        with database(root) as db:
            frames = db.execute("""SELECT d.time_s, d.payload, f.payload FROM decisions d
                LEFT JOIN rf_frames f ON f.time_s=d.time_s ORDER BY d.time_s""")
            rows = zip(frames, record["probabilities"], record["pre_action_connected"], strict=True)
            for index, ((t, decision, previous), probability, before) in enumerate(rows):
                row = unpack(decision)
                connected = bool(unpack(previous)["metrics"]["search_connected"]) if previous is not None else None
                if (t != index * .5 or row["decision_time_s"] != t or "fleet_onset_probability" in row
                        or (row["support_count"] > 0) != (probability is not None) or before is not connected):
                    raise ValueError("단절 예측 시각과 비행의 관측 범위가 일치하지 않습니다.")
        return record
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error, zlib.error) as exc:
        # A rejected supplement never overrides the original flight channels.
        logging.getLogger(__name__).warning("비행 %s/%s 예측 기록을 사용할 수 없습니다: %s",
                                             root.parent.name, root.name, exc)
        return None


def forecast_manifest(root, signature, original):
    record = checked_forecast(root, signature, forecast_signature(root, original))
    if record is None:
        return original
    manifest = {**original, "head_definitions": {**original.get("head_definitions", {}),
                                                "fleet_onset_probability": FLEET_FORECAST_HEAD}}
    if record.get("model_threshold") is not None:
        manifest["model_threshold"] = record["model_threshold"]
    return manifest


@lru_cache(maxsize=4)
def overview_bytes(root, signature, forecast_file=None):
    # Each record is already validated compact JSON. Keep only the compressed
    # result in memory while streaming this flight's three ordered channels.
    supplement = checked_forecast(root, signature, forecast_file)
    buffer = io.BytesIO()
    with database(root) as db, gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=2, mtime=0) as out:
        out.write(b"{")
        for index, table in enumerate(CHANNELS):
            if index:
                out.write(b",")
            out.write(b'"' + table.encode("ascii") + b'":[')
            for ordinal, (blob,) in enumerate(db.execute(f"SELECT payload FROM {table} ORDER BY time_s")):
                if ordinal:
                    out.write(b",")
                if table == "decisions" and supplement is not None:
                    row = unpack(blob)
                    row["fleet_onset_probability"] = supplement["probabilities"][ordinal]
                    row["pre_action_connected"] = supplement["pre_action_connected"][ordinal]
                    out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
                else:
                    out.write(raw_json(blob))
            out.write(b"]")
        out.write(b"}")
    return buffer.getvalue()


@lru_cache(maxsize=12)
def margin_series(root, signature):
    with database(root) as db:
        times, values, edges, pairs, paths, active = [], [], [], [], [], []
        pair_index, path_index = {}, {}
        warning = False
        for (blob,) in db.execute("SELECT payload FROM rf_frames ORDER BY time_s"):
            frame = unpack(blob)
            times.append(frame["interval_start_s"])
            exact = frame.get("display_margin")
            if exact:
                margin = exact["value"]
                warning = margin >= 0 and (margin < 7 if warning else margin <= 5)
                values.append(margin)
                edges.append(exact["edge"])
                pairs.append(pair_index.setdefault(tuple(exact["pair"]),len(pair_index)))
                paths.append(path_index.setdefault(tuple(exact["path"]),len(path_index)))
                active.append(warning)
            else:
                values.append(frame["metrics"].get("network_margin_db"))
    result = dict(revision=root.name,times=times,values=values)
    if edges:
        if len(edges) != len(times):
            raise ValueError("비행 도중 통신 여유 산출 기준이 달라졌습니다.")
        result.update(bottleneckEdges=edges,pairIndices=pairs,pathIndices=paths,warningActive=active,
                      requiredPairs=list(pair_index),paths=list(path_index), exact=True)
    return result


def observations(root, m, observer, peer, start, stop, cursor):
    fleet = {node["id"] for node in m["fleet"]}
    if observer == peer or observer not in fleet or peer not in fleet:
        raise HTTPException(422, "해당 비행에서 서로 다른 두 기체를 선택하세요.")
    if not all(math.isfinite(t) for t in (start, stop, cursor)) or start < 0 or stop < start:
        raise HTTPException(422, "조회할 시간 구간을 확인하세요.")
    def observed_row(blob):
        row = unpack(blob)
        for field in ("values", "valid", "ages"):
            row[field] = {RADIO_FIELDS.get(key, key): value for key, value in row[field].items()}
        return row
    with database(root) as db:
        rows = [observed_row(blob) for (blob,) in db.execute(
            "SELECT payload FROM observations WHERE observer=? AND peer=? AND time_s BETWEEN ? AND ? ORDER BY time_s",
            (observer, peer, start, stop))]
        latest = db.execute(
            "SELECT payload FROM observations WHERE observer=? AND peer=? AND time_s<=? ORDER BY time_s DESC LIMIT 1",
            (observer, peer, cursor)).fetchone()
    return {"run_id": m["run_id"], "revision": root.name, "observations": rows,
            "at": {"observations": [observed_row(latest[0])] if latest else []}}


def register_team_replay_routes(app, output_root):
    """Register pinned, read-only endpoints for the regional page's existing renderer."""
    if output_root is None:
        return
    bundles = Path(output_root).resolve() / "bundles"
    prefix = "/part2/team/{rid}/{revision}"

    def record(rid, revision):
        if not all(ID.fullmatch(value) for value in (rid, revision)):
            raise HTTPException(404, "팀 비행을 찾을 수 없습니다.")
        try:
            root = confined(bundles, Path(rid) / revision, must_exist=False)
            if root != bundles / rid / revision:
                raise ValueError("요청한 실행·변환본 경로가 다릅니다.")
            signature = []
            for name in ("READY", "manifest.json", "scene.json", "data.sqlite"):
                stats = confined(root, name).stat()
                signature.append((stats.st_mtime_ns, stats.st_size))
            signature = tuple(signature)
            return root, signature, checked_record(root, signature)
        except (KeyError, ValueError, OSError, sqlite3.Error, zlib.error) as exc:
            raise HTTPException(404, "완료한 팀 비행을 확인할 수 없습니다.") from exc

    @app.get(prefix + "/descriptor", include_in_schema=False)
    def descriptor(rid: str, revision: str):
        from .team_evaluation import reference_descriptor
        from .team_incidents import incident_descriptor
        from .team_baseline_record import load_comparison, nominal_bundle
        root, sig, (m, _, events) = record(rid, revision)
        m = forecast_manifest(root, sig, m)
        replay = None
        if rid.startswith("team-") and m["mode"] != "nominal":
            baseline = nominal_bundle(root)
            if (baseline / "READY").is_file():
                _, _, (nm, _, _) = record(baseline.parent.name, revision)
                pair = load_comparison(root)
                if pair:
                    arm = pair["nominal"]
                    pin = m.get("nominal_replay")
                    if (not all(nm[k] == arm[k] for k in ("run_id", "bundle_revision", "region", "team_commit",
                                "map_hash", "seed", "kind", "direction", "model_sha256", "controller", "pair_key",
                                "mode", "simulation_end_s", "mission_completed"))
                            or any(nm["final_metrics"].get(k) != v for k,v in arm["final_metrics"].items())
                            or (pin and (pin["run_id"] != nm["run_id"] or pin["revision"] != revision
                                         or pin["manifest_sha256"] != read_json(baseline/"READY")["manifest_sha256"]))):
                        raise HTTPException(409, "기본 대형 재생과 비교표의 비행이 일치하지 않습니다.")
                    replay = {"run_id":nm["run_id"], "revision":revision}
        keys = ("run_id", "bundle_revision", "adapter_id", "research_version", "region", "map_hash", "seed",
                "kind", "direction", "split", "mode", "recorded_mode", "export_status", "artifact_kind",
                "team_commit", "mission_completed", "simulation_end_s", "final_metrics")
        return json_response(dict(row={k: m.get(k) for k in keys}, manifest=m, nominal_replay=replay,
                                  evaluation=reference_descriptor(root, m),
                                  incidents=incident_descriptor(m, events), margin=margin_series(root, sig)))

    @app.get(prefix + "/manifest", include_in_schema=False)
    def manifest(rid: str, revision: str):
        root, sig, (m, _, _) = record(rid, revision)
        return json_response(forecast_manifest(root, sig, m))

    @app.get(prefix + "/scene", include_in_schema=False)
    def scene(rid: str, revision: str):
        return json_response(record(rid, revision)[2][1])

    @app.get(prefix + "/comparison", include_in_schema=False)
    def comparison(rid: str, revision: str):
        from .team_baseline_record import load_comparison
        root, _, (manifest, _, _) = record(rid, revision)
        identity = dict(run_id=rid, revision=revision, team_commit=manifest["team_commit"])
        try:
            pair = load_comparison(root)
        except FileNotFoundError:
            pair = None
        except (ValueError, OSError, KeyError, TypeError) as exc:
            logging.getLogger(__name__).warning("Invalid paired comparison for %s/%s: %s", rid, revision, exc)
            return json_response(dict(identity, status="error", message="두 비행의 버전과 기록을 확인할 수 없습니다."))
        if pair is None:
            return json_response(dict(identity, status="pending", message="같은 조건의 기본 대형 비행 기록을 기다리는 중"))
        return json_response(dict(identity, status="ready", nominal=pair["nominal"], applied=pair["applied"]))

    @app.get(prefix + "/events", include_in_schema=False)
    def events(rid: str, revision: str):
        return json_response(dict(run_id=rid, revision=revision, events=record(rid, revision)[2][2]))

    @app.get(prefix + "/overview", include_in_schema=False)
    def overview(rid: str, revision: str):
        root, sig, (m, _, _) = record(rid, revision)
        return zipped(overview_bytes(root, sig, forecast_signature(root, m)))

    @app.get(prefix + "/observations", include_in_schema=False)
    def radio(rid: str, revision: str, observer: str, peer: str, start: float = Query(0., alias="from"),
              to: float | None = None, at: float = 0.):
        root, _, (m, _, _) = record(rid, revision)
        return json_response(observations(root, m, observer, peer, start, m["simulation_end_s"] if to is None else to, at))

    @app.get(prefix + "/model", include_in_schema=False)
    def model(rid: str, revision: str):
        from .team_model import model_for_flight
        return json_response(model_for_flight(record(rid, revision)[2][0]))

    @app.get(prefix + "/evaluation", include_in_schema=False)
    def evaluation(rid: str, revision: str):
        from .team_evaluation import read_reference
        root, _, (m, _, _) = record(rid, revision)
        value = read_reference(root, m)
        if value is None:
            raise HTTPException(404, "선택한 비행의 지역 예측 평가 기록이 없습니다.")
        return json_response(value)

    @app.get(prefix + "/incident/{event_id}", include_in_schema=False)
    def incident(rid: str, revision: str, event_id: str):
        from .team_incidents import read_incident
        root, _, (m, _, events) = record(rid, revision)
        if event_id not in {"book", "analysis"} and not any(
            e["id"] == event_id and e["type"] == "persistent_search_outage" for e in events
        ):
            raise HTTPException(404, "해당 비행의 사건을 찾을 수 없습니다.")
        if event_id == "analysis":
            from .team_analysis import read_analysis
            return json_response(read_analysis(root, m))
        return json_response(read_incident(root, m, event_id, events))
