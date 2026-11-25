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
    return {"region": manifest["region"], "mode": flight_mode(manifest), "revision": manifest["bundle_revision"],
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
    if not receipt or receipt["start_s"] < start or receipt["offset_xy_m"] != command:
        return action
    arrival = receipt["start_s"]
    action.update(command_received=True, delivered_at_s=arrival, applied_correction=command)
    # A bracket spanning reception can contain movement from the previous
    # command. Compare two complete saved positions after delivery.
    motion_start = math.ceil((arrival - 1e-8) / step) * step
    end = motion_start + step
    if end > duration:
        return action
    if any(other["related_uav_ids"][0] == relay and arrival < other["start_s"] < end
           for other in receipts.values()):
        return action
    first, last = _position(db, motion_start, relay), _position(db, end, relay)
    if first and last:
        delta = [last[k] - first[k] for k in ("x", "y")]
        action.update(actual_delta=delta, moved_m=math.hypot(*delta), motion_start_s=motion_start, outcome_available_s=end,
                      realized_endpoint=[last["x"],last["y"]], offset_receipt_and_motion=True)
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
    chosen = {a["time_s"]:a for a in during}
    prior = [a for a in actions if left <= a["time_s"] < start]
    decisions = []
    for (blob,) in db.execute("SELECT payload FROM decisions WHERE time_s>=? AND time_s<? ORDER BY time_s", (start, end)):
        d = _payload(blob)
        time = d["decision_time_s"]
        dec = dict(time_s=time,early_score=d.get("fleet_score"),threshold=manifest.get("model_threshold"))
        if time in chosen:
            # Reuse only the recorded action classification; no inference about the cause.
            dec["reason"] = {"prevention":"added_prevention","recovery":"team_recovery"}.get(chosen[time]["reason"])
        decisions.append(dec)
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
        actions = [dict(_action(db,e,receipts,manifest["clocks"]["rf_dt_s"],manifest["simulation_end_s"]),
                        threshold=manifest.get("model_threshold")) for e in rows if e["type"] == "intervention"]
        outages = [e for e in rows if e["type"] == "search_outage"]
        cases = [_case(db, e, manifest, outages, actions) for e in rows if e["type"] == "persistent_search_outage"]
    return {"run_id": rid, "revision": revision, "region": manifest["region"], "mode": flight_mode(manifest), "events": cases}


def read_incident(root, manifest, event_id, events):
    if event_id == "analysis":
        from .team_analysis import read_analysis
        return read_analysis(Path(root), manifest)
    book = _book(Path(root), manifest["run_id"], manifest["bundle_revision"], manifest["artifacts"]["data.sqlite"]["sha256"])
    if event_id == "book":
        return book
    for case in book["events"]:
        if case["id"] == event_id:
            return case
    raise KeyError(event_id)


def flight_mode(manifest):
    return "nominal" if manifest.get("mode") == "nominal" else "v21"


def timeline_events(db, rows):
    """Join published request, actual offset receipt and measured interval by their native clocks."""
    from collections import defaultdict
    by_relay = defaultdict(list)
    receipts = {}
    for e in rows:
        if e["type"] == "applied_command":
            relay = e["related_uav_ids"][0]
            by_relay[relay].append(e)
            receipts[relay,e["source_command_s"]] = e
    if not by_relay and not any(e["type"] == "intervention" for e in rows):
        return rows
    duration = db.execute("SELECT MAX(time_s) FROM rf_frames").fetchone()[0]
    frame = db.execute("SELECT interval_end_s - interval_start_s FROM rf_frames ORDER BY time_s LIMIT 1").fetchone()
    if not duration or not frame:
        return rows
    for e in rows:
        if e["type"] != "intervention":
            continue
        action = _action(db,e,receipts,frame[0],duration)
        e.update(team_receipt_clock=True, **{k:action[k] for k in
            ("actual_pre_action_connected","actual_search_connected_at_command","command_received",
             "delivered_at_s","realized_endpoint","outcome_available_s","early_score") if k in action})
    for relay,changes in by_relay.items():
        start = None
        def interval(stop, open_end):
            return dict(id="relocation-"+start["id"], type="relocation", start_s=start["start_s"],end_s=stop,
                duration_s=stop-start["start_s"],temporal_kind="interval",scope="relay",related_uav_ids=[relay],
                definition_id="nonzero_applied_offset",derived=True,source_refs=["applied_command"],
                left_censored=start["start_s"]==0,right_censored=open_end,actual_movement_is_separate=True)
        for change in sorted(changes,key=lambda e:e["start_s"]):
            if any(change["offset_xy_m"]):
                if start is None:
                    start = change
            elif start is not None:
                rows.append(interval(change["start_s"], False))
                start = None
        if start is not None and start["start_s"] < duration:
            rows.append(interval(duration, True))
    return sorted(rows, key=lambda event: (event["start_s"], event["id"]))
