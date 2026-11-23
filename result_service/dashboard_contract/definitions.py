"""Audited v5 data semantics. No research imports or model evaluation."""
from .validation import canonical_hash
VERSION = "uav-v5-definitions-1"
FEATURE_NAMES = [
"self_velocity_x_mps","self_velocity_y_mps","self_altitude_m","self_is_relay","peer_is_relay",
"tx_attempts_in_interval","ack_successes_in_interval","ack_success_ratio_in_interval",
"last_ack_received_power_dbm","last_ack_sinr_db","seconds_since_last_ack","self_queue_packets",
"self_position_x_m","self_position_y_m","inbound_successes_in_interval","seconds_since_peer_state_timestamp",
"estimated_relative_x_m","estimated_relative_y_m","last_received_relative_velocity_x_mps",
"last_received_relative_velocity_y_mps","estimated_peer_distance_m","estimated_distance_rate_mps",
"estimated_path_loss_now_db","estimated_path_loss_after_5s_db","estimated_los_now","estimated_los_after_5s",
"estimated_path_loss_change_5s_db","self_distance_to_ge40m_building_m",
"beacon_received_power_dbm","beacon_received_sinr_db","beacon_age_s","peer_reported_reverse_sinr_db",
"reverse_measurement_age_s","beacons_received_this_interval","beacon_relative_x_m","beacon_relative_y_m",
"beacon_relative_vx_mps","beacon_relative_vy_mps","beacon_estimated_distance_m","beacon_estimated_distance_rate_mps",
"observed_minimum_sinr_db","observed_bidirectional_margin_db","inbound_sinr_slope_3s","reverse_sinr_slope_3s",
"beacons_received_last_5s","beacons_received_last_10s","both_measurements_fresh_2s","self_acceleration_mps2"]
GRAPH_NAMES = ["search_connected","search_pair_reachability","fleet_connected","components",
"largest_component_fraction","mean_degree","isolated_fraction","edge_density","graph_efficiency","reachable_search_hops"]
PACKET_NAMES = ["generated_packets","delivered_packets","pdr_so_far","observed_target_fraction",
"information_shared_fraction","queued_packets","search_connected","search_pair_reachability","aoi_s",
"tx_attempts","successful_hops","ttl_drops","retry_drops","overflow_drops","observed_cells",
"shared_cells","timely_pdr_so_far","relay_successful_hops"]
HEAD_NAMES = ["risk_5s","onset_3to4s","onset_3to7s","onset_0to7s"]
HEAD_SLICES = ["pair[k+1:k+10]","start[k+7:k+10]","start[k+7:k+16]","start[k+1:k+16]"]
HEAD_OFFSETS = [(0.,5.),(3.,4.5),(3.,7.5),(0.,7.5)]
REWARD_NAMES = ["outage_s","burst","packet_quality","extra_motion","search_lag","protection","time","failure"]
CLOCKS = {
"motion-v5-1":{"id":"motion-v5-1","step_s":3.0,"rule":"boundary k at 3*k; linear interpolation only inside a known step","time_provenance":"derived_from_contract"},
"rf-interval-v5-1":{"id":"rf-interval-v5-1","step_s":0.5,"rule":"r covers [0.5*r,0.5*(r+1)); channel evaluated at start","time_provenance":"derived_from_contract"},
"observation-v5-1":{"id":"observation-v5-1","step_s":0.5,"rule":"RF/packet observation available at interval end; no future interpolation","time_provenance":"derived_from_contract"},
"decision-v5-1":{"id":"decision-v5-1","step_s":3.0,"rule":"decision d generated at 3*d from observations completed by 3*d","time_provenance":"derived_from_contract"},
"cohort-v5-1":{"id":"cohort-v5-1","step_s":3.0,"rule":"generation interval [3*d,3*(d+1)); final settlement available time unrecorded; posthoc only","time_provenance":"unknown"},
"drain-v5-1":{"id":"drain-v5-1","duration_s":2.0,"rule":"summary includes 2-second queue drain; packet_frames end before drain","time_provenance":"derived_from_contract"}}
EVENTS = {
"search-outage-v5-1":{"id":"search-outage-v5-1","version":"1","scope":"search_network","rule":"Consecutive C[:,0]==0 RF intervals; initial down left censored; final down right censored; no recovery hysteresis","resolution_s":0.5,"source":"cooperative/packets.py:154"},
"link-onset-v5-1":{"id":"link-onset-v5-1","version":"1","scope":"observer_peer","rule":"relay observer 3..7; up->bad then next RF bad; initial down excluded; at least 1s; onset q*.5; confirmed after second bad interval","resolution_s":0.5,"source":"solo/metrics.py:20"},
"relocation-v5-1":{"id":"relocation-v5-1","version":"1","scope":"relay","rule":"Consecutive decisions with nonzero applied correction; commanded correction interval, not a causal success claim or every physical movement","resolution_s":3.0,"source":"solo/rollout.py:79"},
"first-alert-v5-1":{"id":"first-alert-v5-1","version":"1","scope":"observer_peer","rule":"Stored enabled rule only; 3s decisions, warmup21, tail8; support & finite; enter/exit hysteresis; no fitting","resolution_s":3.0,"source":"solo/metrics.py:32"}}
LABELS = {
"last_ack_received_power_dbm":"최근 ACK 수신전력","last_ack_sinr_db":"최근 ACK SINR","seconds_since_last_ack":"ACK 측정 나이",
"beacon_received_power_dbm":"비콘 수신전력","beacon_received_sinr_db":"비콘 SINR","beacon_age_s":"비콘 나이",
"peer_reported_reverse_sinr_db":"상대가 보고한 역방향 SINR","reverse_measurement_age_s":"역방향 측정 나이",
"observed_bidirectional_margin_db":"관측 양방향 여유","observed_minimum_sinr_db":"관측 최소 SINR",
"ack_success_ratio_in_interval":"구간 ACK 성공률","estimated_path_loss_now_db":"현재 추정 경로손실",
"estimated_path_loss_after_5s_db":"5초 후 추정 경로손실","tx_attempts_in_interval":"구간 전송시도",
"ack_successes_in_interval":"구간 ACK 성공","self_queue_packets":"송신 큐 패킷","both_measurements_fresh_2s":"양방향 2초 내 측정"}
def observation_definitions():
    out={}
    for i,name in enumerate(FEATURE_NAMES):
        unit = "dBm" if name.endswith("_dbm") else "dB" if name.endswith("_db") else "m/s²" if name.endswith("_mps2") else "m/s" if name.endswith("_mps") else "m" if name.endswith("_m") else "s" if name.endswith("_s") else "boolean" if name.endswith("_relay") or "los_" in name or name=="both_measurements_fresh_2s" else "ratio" if "ratio" in name else "count"
        if i in (42,43): unit="dB/s"
        if i in (10,15): unit="s"
        kind = "estimated" if i in range(16,28) or i in range(34,44) else "measured"
        direction = "own_state" if i in (0,1,2,3,11,12,13,27,47) else "peer_to_observer"
        age = None
        if i in (8,9): age="seconds_since_last_ack"
        elif i in (28,29,34,35,36,37,38,39): age="beacon_age_s"
        elif i==31: age="reverse_measurement_age_s";direction="observer_to_peer_reported_by_peer"
        elif i in (40,41): age="maximum_bidirectional_measurement_age_s";direction="bidirectional_observed"
        elif i in (16,17,18,19,20,21,22,23,24,25,26):
            age="seconds_since_peer_state_timestamp"
            direction="observer_to_peer_estimated" if i in (22,23,24,25,26) else "peer_relative_to_observer"
        elif i==42: direction="peer_to_observer"
        elif i==43: direction="observer_to_peer_reported_by_peer"
        elif i in (5,6,7): direction="observer_to_peer_data_with_reverse_ACK"
        out[name]=dict(id=name,label=LABELS.get(name,name.replace("_"," ")),unit=unit,kind=kind,
            direction_template=direction,age_metric_id=age,definition_version=VERSION,
            source="usable/collect.py::FEATURES + usable/beacons.py::EXTRA_NAMES; cooperative/telemetry.py",
            missing_rule="rf_masks=false => null/not_collected; mask=true and value=0 remains valid",
            target_offset_s=5.0 if i in (23,25) else 0.0)
        out[name]["definition_hash"]=canonical_hash(out[name])
    return out

# label, unit, denominator, aggregation, scope, preferred direction
CORE = {
"search_connected_mean":("수색망 연결 유지율","ratio","RF intervals","mean C[:,0]","run_total","higher"),
"pair_reach_mean":("수색 쌍 도달률","ratio","search unordered pairs, then RF intervals","mean C[:,1]","run_total","higher"),
"pdr":("종단간 PDR","ratio","all generated packets","delivered/generated including 2s drain","run_total","higher"),
"timely_pdr":("0.25초 적시 전달률","ratio","all generated packets","delivered within .25s/generated including drain","run_total","higher"),
"information_shared_fraction":("정보 공유율","ratio","all outdoor target cells","intersection of all search known cells / targets","run_total","higher"),
"observable_coverage":("관측 가능 영역 수색률","ratio","observable outdoor cells","observed/observable","run_total","higher"),
"outage_total_s":("누적 단절","s","RF intervals","sum false search_connected * .5","run_total","lower"),
"outage_max_s":("최장 단절","s","contiguous disconnected intervals","max disconnected run * .5","run_total","lower"),
"outage_events":("수색망 단절 횟수","count","contiguous disconnected intervals","count runs including initial down","run_total","lower"),
"duration_s":("임무 시간","s","mission","control steps*3, before packet drain","run_total","context"),
"baseline_duration_s":("기준 임무 시간","s","baseline mission","saved baseline duration","run_total","context"),
"duration_ratio":("기준 대비 시간비","ratio","baseline_duration_s","duration_s/baseline_duration_s","run_total","lower"),
"generated_packets":("생성 패킷","count","all generated packets","count, generation before drain","run_total","context"),
"delivered_packets":("전달 패킷","count","generated packets","delivered including drain","run_total","higher"),
"delay_mean_s":("평균 전달 지연","s","delivered packets only","conditional mean delay","run_total","lower"),
"delay_p95_s":("전달 지연 95백분위","s","delivered packets only","conditional 95th percentile","run_total","lower"),
"aoi_mean_s":("평균 정보 나이","s","search directed pairs and RF intervals","mean end-of-frame current time minus last delivered packet birth timestamp; initial timestamp0","run_total","lower"),
"search_travel_m":("수색기 이동 거리","m","search UAV steps","sum actual XY displacement norms","run_total","context"),
"relay_travel_m":("중계기 이동 거리","m","relay UAV steps","sum actual XY displacement norms","run_total","context"),
"mission_completed":("임무 완료","boolean","mission","stored mission completion; process status separate","run_total","higher"),
"retries":("재전송","count","hop attempts","retry attempt count including drain","run_total","lower"),
"ttl_drops":("수명 만료 폐기","count","generated packets","final expired status count","run_total","lower"),
"retry_drops":("재시도 한도 폐기","count","generated packets","final retry-limit status count","run_total","lower"),
"overflow_drops":("큐 초과 폐기","count","generated packets","final overflow status count","run_total","lower"),
"minimum_separation_m":("최소 연속 이격거리","m","all moving UAV pairs","swept minimum across motion segments","run_total","higher"),
"search_formation_rmse_m":("수색 대형 거리 오차","m","search pairs over position boundaries","RMS(actual pair distance - nominal pair distance)","run_total","lower"),
"actual_safety_violations":("실제 안전 위반","count","trajectory","stored audited violation count","run_total","lower"),
"reward_sum":("총 보상","reward","settled decision cohorts","sum communications-priority-v3 costs","run_total","higher"),
"reward_mean":("평균 보상","reward","settled decision cohorts","mean communications-priority-v3 costs","run_total","higher"),
"relocation_fraction":("보정 적용 비율","ratio","3s decisions","nonzero applied correction decisions / decisions","run_total","context"),
"protection_fraction":("임무 보호 비율","ratio","3s decisions","protected decisions / decisions","run_total","context"),
"command_switch_count":("명령 변경 횟수","count","relay decisions","count categorical action switches","run_total","context"),
"relay_stationary_fraction":("중계기 정지 비율","ratio","relay steps","fraction actual XY displacement norm <1e-9","run_total","context"),
"beacon_tx":("비콘 송신","count","mission","1Hz per UAV beacon transmission count","run_total","context"),
"beacon_rx":("비콘 수신","count","mission","successful directed beacon receptions","run_total","context"),
}
def metric_definition(key):
    if key in CORE:
        label,unit,denom,aggregation,scope,direction=CORE[key]
        known=True
    else:
        label,unit,denom,aggregation,scope,direction=key,"unknown","unverified","stored original scalar, no redefinition","run_total","context"
        known=False
    definition=dict(id=f"v5.{key}",key=key,label=label,unit=unit,denominator=denom,aggregation=aggregation,
      scope=scope,preferred_direction=direction,version=VERSION,definition_status="verified" if known else "unverified",
      comparable=known,source="cooperative/world.py::metrics; cooperative/continuity_world.py; solo/rollout.py",
      zero_denominator="Preserve source stored convention; display sample-zero limitation; new undefined calculations null")
    definition["definition_hash"]=canonical_hash(definition)
    return definition
def head_definitions():
    return {name:dict(id=name,version=VERSION,raw_head=i,output_semantics="model_probability_not_calibrated_claim",
       exact_discrete_rule=HEAD_SLICES[i],anchor="k=6*decision-1; pair=bad[:-1]&bad[1:]; start requires up->bad plus next bad",
       nominal_target_offsets_s=HEAD_OFFSETS[i],input_cutoff_rule="completed observations <= decision_time_s",
       target_rule_source="solo/targets.py",labels_posthoc_only=True)
       for i,name in enumerate(HEAD_NAMES)}


def series_definitions():
    """Frame fields remain distinct from final summary metrics and drain."""
    result={}
    for name in GRAPH_NAMES:
        unit={"components":"count","mean_degree":"count","reachable_search_hops":"hops",
              "search_connected":"boolean","fleet_connected":"boolean"}.get(name,"ratio")
        result[name]=dict(id="v5.frame."+name,key=name,unit=unit,scope="instant",
            aggregation="graph state for RF interval; no packet drain",clock_rule_id="rf-interval-v5-1",
            available_at="interval_start_s",definition_version=VERSION,
            source="swarm_sim/fast_network.py::graph_statistics; C",label=name.replace("_"," "))
    for name in PACKET_NAMES:
        if name in result: continue
        unit="ratio" if name in ("pdr_so_far","observed_target_fraction","information_shared_fraction","timely_pdr_so_far") else "s" if name=="aoi_s" else "count"
        scope="instant" if name in ("queued_packets","aoi_s","observed_target_fraction","information_shared_fraction","observed_cells","shared_cells") else "cumulative_to_t"
        result[name]=dict(id="v5.frame."+name,key=name,unit=unit,scope=scope,
            aggregation="stored packet frame at RF end, before final 2s drain",clock_rule_id="observation-v5-1",
            available_at="interval_end_s",definition_version=VERSION,
            source="cooperative/packets.py::packet_interval; packet_frames",label=name.replace("_"," "))
    for item in result.values(): item["definition_hash"]=canonical_hash(item)
    return result
