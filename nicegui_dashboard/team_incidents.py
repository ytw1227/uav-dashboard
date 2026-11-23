"""Build the existing incident player's event book from a single sealed team flight."""
from contextlib import closing
from functools import lru_cache
import json
import math
from pathlib import Path
import sqlite3
import zlib


def incident_descriptor(manifest, events):
    pin = manifest["run_id"] + "/" + manifest["bundle_revision"]
    return {"region": manifest["region"], "mode": "v21", "revision": manifest["bundle_revision"],
            "events_chunk": "team-book/" + pin, "analysis_chunk": "team-analysis/" + pin}


def _payload(blob):
    return json.loads(blob if isinstance(blob, str) else zlib.decompress(blob))


def _one(db, query, when):
    row = db.execute(query, (when,)).fetchone()
    return _payload(row[0]) if row else None


def _frame(db, start):
    frame = _one(db, "SELECT payload FROM rf_frames WHERE time_s>? ORDER BY time_s LIMIT 1", start)
    return frame if frame and frame["interval_start_s"] == start else None


def _edges(frame):
    return {tuple(sorted((link["source"], link["target"])))
            for link in (frame or {}).get("links", []) if link["available"]}


def _position(db, time, relay):
    row = _one(db, "SELECT payload FROM positions WHERE time_s=?", time)
    if row:
        return next((u for u in row["uavs"] if u["id"] == relay), None)
    return None


def _action(db, request, receipts, step, duration):
    detail = request["source_detail"]
    start = request["start_s"]
    relay = request["related_uav_ids"][0]
    command = detail["new_offset"]
    before = _frame(db, start - step)
    action = dict(time_s=start, relay=relay, command=command, reason=detail["reason"],
                  early_score=detail.get("probability_early"),
                  actual_pre_action_connected=bool(before["metrics"]["search_connected"]) if before else None,
                  actual_search_connected_at_command=detail.get("actual_connected_at_request"))
    # The network delivers a command after publication. Match that publication
    # and the commanded relay/vector; another command in the same outage may differ.
    receipt = receipts.get((relay, detail.get("published_s")))
    if not receipt or receipt["offset_xy_m"] != command:
        return action
    arrival = receipt["start_s"]
    action.update(command_received=True, delivered_at_s=arrival, applied_correction=command)
    end = (math.floor(arrival / step) + 1) * step
    if end > duration:
        return action
    first, last = _position(db, start, relay), _position(db, end, relay)
    if first and last:
        delta = [last[k] - first[k] for k in ("x", "y")]
        action.update(actual_delta=delta, moved_m=math.hypot(*delta), outcome_available_s=end)
    return action


def _case(db, event, manifest, all_outages, actions):
    start, end = event["start_s"], event["end_s"]
    duration = manifest["simulation_end_s"]
    step = manifest["clocks"]["rf_dt_s"]
    left, right = max(0, start - 15), min(duration, end + 15)
    first = _frame(db, start)
    recovered = _frame(db, end) if end < duration else None
    if not first or first["metrics"]["search_connected"] or (recovered and not recovered["metrics"]["search_connected"]):
        raise ValueError("저장된 링크 시각과 단절 구간을 대조할 수 없습니다.")
    groups = first["components"]
    membership = {u: i for i, group in enumerate(groups) for u in group}
    fleet = manifest["fleet"]
    search = {u["id"] for u in fleet if u["role"] == "search"}
    relays = [u["id"] for u in fleet if u["role"] == "relay"]
    searches = [[u for u in group if u in search] for group in groups]
    broken = sorted(_edges(_frame(db, start - step)) - _edges(first))
    crossing = [(a, b) for a, b in broken if membership[a] != membership[b]]
    during = [a for a in actions if start <= a["time_s"] < end]
    prior = [a for a in actions if left <= a["time_s"] < start]
    decisions = []
    for (blob,) in db.execute("SELECT payload FROM decisions WHERE time_s>=? AND time_s<? ORDER BY time_s", (start, end)):
        d = _payload(blob)
        decisions.append({"time_s": d["decision_time_s"], "early_score": d.get("fleet_score")})
    focus = during[0]["relay"] if during else next((u for pair in crossing for u in pair if u in relays), None)
    pair = {"observer": crossing[0][0], "peer": crossing[0][1]} if crossing else None
    return {**event, "window": [left, right], "confirmation_s": start + 1,
            "window_outages": [[o["start_s"], o["end_s"]] for o in all_outages if o["start_s"] <= right and o["end_s"] > left],
            "recovery_s": end if recovered else None, "components_at_start": groups,
            "search_groups": [group for group in searches if group], "lost_links": broken,
            "crossing_lost_links": crossing, "default_pair": pair, "focus_relay": focus,
            "decisions": decisions, "interventions": during, "prior_interventions": prior}


@lru_cache(maxsize=12)
def _book(root, rid, revision, signature):
    manifest = json.loads((root / "manifest.json").read_bytes())
    if (manifest["run_id"], manifest["bundle_revision"]) != (rid, revision):
        raise ValueError("사건 기록의 실행과 선택한 비행이 다릅니다.")
    uri = (root / "data.sqlite").resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        rows = [_payload(blob) for (blob,) in db.execute("SELECT payload FROM events ORDER BY start_s,event_id")]
        receipts = {(e["related_uav_ids"][0], e["source_command_s"]): e for e in rows if e["type"] == "applied_command"}
        actions = [_action(db, e, receipts, manifest["clocks"]["rf_dt_s"], manifest["simulation_end_s"])
                   for e in rows if e["type"] == "intervention"]
        outages = [e for e in rows if e["type"] == "search_outage"]
        cases = [_case(db, e, manifest, outages, actions) for e in rows if e["type"] == "persistent_search_outage"]
    return {"run_id": rid, "revision": revision, "region": manifest["region"], "mode": "v21", "events": cases}


def read_incident(root, manifest, event_id, events):
    if event_id == "analysis":
        # These recordings have no replay of counterfactual radio propagation.
        return {}
    book = _book(Path(root), manifest["run_id"], manifest["bundle_revision"], manifest["artifacts"]["data.sqlite"]["sha256"])
    if event_id == "book":
        return book
    for case in book["events"]:
        if case["id"] == event_id:
            return case
    raise KeyError(event_id)
