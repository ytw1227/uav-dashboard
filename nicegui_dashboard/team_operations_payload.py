"""Build operation display data from native captured fields."""
from copy import deepcopy


def make_payload(output_root, manifest, row, arrays, progress, checks, *, embedded=False):
    from .team_operations import identity_for, operations_digest, validate_operations
    if embedded:
        from .team_operations import embedded_identity
    progress = deepcopy(progress)
    ids = [item["id"] for item in manifest["fleet"]]
    plan = deepcopy(row["placement"])
    station = plan.get("station")
    fixed = station["relay"] if station else None
    elevated = {item["relay"] for item in plan.get("windows", [])}
    roles = []
    for i, item in enumerate(manifest["fleet"]):
        duty = ("search" if item["role"] == "search" else "fixed_relay" if i == fixed
                else "altitude_relay" if i in elevated else "mobile_relay")
        label = {"search": "수색", "fixed_relay": "고정 중계", "altitude_relay": "고도 조정 중계",
                 "mobile_relay": "일반 중계"}[duty]
        roles.append(dict(drone_id=item["id"], role=item["role"], duty=duty, label=label))
    windows = [dict(deepcopy(item), id=f"altitude-window-{i}", drone_id=ids[item["relay"]])
               for i, item in enumerate(row["altitude_windows"])]
    moves = [dict(deepcopy(item), drone_id=ids[item["relay"]]) for item in row["altitude_moves"]]
    count = len(arrays["network"])
    for i, sample in enumerate(progress):
        sample["network_connected"] = bool(arrays["network"][i]) if i < count else None
        sample["network_margin_db"] = float(arrays["display_margin"][i]) if i < count else None
        if i < count and sample["heights_m"] != arrays["altitudes"][i].astype(float).tolist():
            # The source stores altitudes as float32. Compare in that same representation.
            import numpy as np
            if not np.array_equal(np.asarray(sample["heights_m"], np.float32), arrays["altitudes"][i]):
                raise ValueError("운용 단계 고도와 원본 위치 고도가 다릅니다.")
            sample["heights_m"] = arrays["altitudes"][i].astype(float).tolist()
    if "end_altitudes_m" in row and progress[-1]["heights_m"] != row["end_altitudes_m"]:
        raise ValueError("운용 기록의 마지막 고도와 원 비행 종료 고도가 다릅니다.")
    summary = dict(coverage=float(row["coverage"]), waypoints_reached=int(row["waypoints_reached"]),
                   waypoints_total=int(row["waypoints_total"]), duration_s=float(row["duration_s"]),
                   verified_crossings=sum(item["outcome"] == "crossed" for item in row["altitude_windows"]),
                   cleared_stages=int(row["cleared_stages"]), clearance_edges=int(row["clearance_edges"]),
                   mission_completed=bool(row["mission_completed"]))
    payload = dict(schema_version=1, status="available", identity=embedded_identity(manifest) if embedded else identity_for(output_root, manifest),
                   plan=plan, roles=roles, windows=windows, moves=moves, progress=progress, checks=checks, summary=summary,
                   verification=dict(method="same-commit observational replay; exact existing channel comparison",
                                     tables=["positions", "rf_frames", "decisions", "observations", "events", "metrics",
                                             "incident_analysis"], original_bundle_unchanged=True),
                   definitions=dict(progress_sample_s=.5, source_update_s=.1,
                                    altitude="native per-aircraft altitude, metres",
                                    coverage="native visible survey cells reached / observable cells",
                                    state="navigation state after native update; no predicted stage times",
                                    complete="survey waypoint path completed; the native final sharing interval may still be running",
                                    clearing="native clearance navigation after waypoint progress stalls; safe detour or parking move, not information sharing",
                                    stalled_s="time without waypoint-stage advance; not physical standstill",
                                    checks="unmodified native callback return; no extra policy or RF evaluation"))
    payload["definitions"]["terminal_snapshot"] = (
        "at mission-budget expiry, read the final native state after its last 0.1-second update; "
        "no extra navigation update or RF evaluation; observed_after_update_s records its source time")
    if embedded:
        payload["verification"] = dict(method="passive native navigation callbacks in the original capture",
                                       source="native_navigation_callbacks", native_callbacks_once=True,
                                       original_bundle_unchanged=True)
    payload["payload_sha256"] = operations_digest(payload)
    validate_operations(payload, manifest, payload["identity"])
    return payload
