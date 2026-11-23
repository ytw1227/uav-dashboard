"""Read a sealed team flight into the same display channels as the regional player."""
from contextlib import closing
from functools import lru_cache
import gzip
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import zlib

from fastapi import HTTPException, Query
from fastapi.responses import Response

from result_service.dashboard_contract.validation import confined, read_json, sha256, validate_manifest
from .package_web import compressed_json


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
    with database(root) as db:
        rows = [unpack(blob) for (blob,) in db.execute("SELECT payload FROM events ORDER BY start_s,event_id")]
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
    return {**manifest, "recorded_mode": manifest["mode"], "mode": "v21",
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


@lru_cache(maxsize=4)
def overview_bytes(root, signature):
    # Each record is already validated compact JSON. Keep only the compressed
    # result in memory while streaming this flight's three ordered channels.
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
                out.write(raw_json(blob))
            out.write(b"]")
        out.write(b"}")
    return buffer.getvalue()


@lru_cache(maxsize=12)
def margin_series(root, signature):
    with database(root) as db:
        times, values = [], []
        for (blob,) in db.execute("SELECT payload FROM rf_frames ORDER BY time_s"):
            frame = unpack(blob)
            times.append(frame["interval_start_s"])
            values.append(frame["metrics"].get("network_margin_db"))
    # No bottleneck aircraft/link ID was saved. Never infer IDs from the scalar.
    return {"revision": root.name, "times": times, "values": values}


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
        from .team_incidents import incident_descriptor
        root, sig, (m, _, events) = record(rid, revision)
        keys = ("run_id", "bundle_revision", "adapter_id", "research_version", "region", "map_hash", "seed",
                "kind", "direction", "split", "mode", "recorded_mode", "export_status", "artifact_kind",
                "team_commit", "mission_completed", "simulation_end_s", "final_metrics")
        return json_response(dict(row={k: m.get(k) for k in keys}, manifest=m,
                                  incidents=incident_descriptor(m, events), margin=margin_series(root, sig)))

    @app.get(prefix + "/manifest", include_in_schema=False)
    def manifest(rid: str, revision: str):
        return json_response(record(rid, revision)[2][0])

    @app.get(prefix + "/scene", include_in_schema=False)
    def scene(rid: str, revision: str):
        return json_response(record(rid, revision)[2][1])

    @app.get(prefix + "/events", include_in_schema=False)
    def events(rid: str, revision: str):
        return json_response(dict(run_id=rid, revision=revision, events=record(rid, revision)[2][2]))

    @app.get(prefix + "/overview", include_in_schema=False)
    def overview(rid: str, revision: str):
        root, sig, _ = record(rid, revision)
        return zipped(overview_bytes(root, sig))

    @app.get(prefix + "/observations", include_in_schema=False)
    def radio(rid: str, revision: str, observer: str, peer: str, start: float = Query(0., alias="from"),
              to: float | None = None, at: float = 0.):
        root, _, (m, _, _) = record(rid, revision)
        return json_response(observations(root, m, observer, peer, start, m["simulation_end_s"] if to is None else to, at))

    @app.get(prefix + "/incident/{event_id}", include_in_schema=False)
    def incident(rid: str, revision: str, event_id: str):
        from .team_incidents import read_incident
        root, _, (m, _, events) = record(rid, revision)
        if event_id not in {"book", "analysis"} and not any(
            e["id"] == event_id and e["type"] == "persistent_search_outage" for e in events
        ):
            raise HTTPException(404, "해당 비행의 사건을 찾을 수 없습니다.")
        return json_response({} if event_id == "analysis" else read_incident(root, m, event_id, events))
