import {esc,finite,num,fmt} from './render.js';
const reasons = {
 added_cut_recovery: ['관측이 끊긴 수색 드론의 복구 보정을 선택했습니다.', '수색 드론의 비콘 관측 공백을 근거로 계획 경로와 재연결 가능성을 검토했습니다. 실제 수색망 단절 여부는 별도 기록으로 확인합니다.'],
 fleet_transformer_prevention: ['편대 예측에 따라 예방 보정을 선택했습니다.', '새 모델의 혼합 위험 점수와 안전·통신 개선 조건을 만족한 이동 명령입니다.'],
 not_evaluated: ['추가 판단을 실행하지 않았습니다.', ''],
 warmup: ['아직 경로 보정을 판단하지 않았습니다.', '비행 초기의 관측을 모으는 중입니다.'],
 retained_v18: ['기존 예방·복구 보정을 유지했습니다.', '제어기가 선택한 보정의 종류와 대상은 사건 기록에서 확인할 수 있습니다.'],
 added_prevention: ['예방 보정을 추가했습니다.', '단절 위험에 대비해 중계 드론의 이동 명령을 바꿨습니다.'],
 below_advance_threshold: ['예방 보정을 추가하지 않았습니다.', '점수가 예방 기준보다 낮아 기본 정책이 선택한 이동 명령을 유지했습니다.'],
 no_physical_prevention_evidence: ['예방 보정을 추가하지 않았습니다.', '예상 통신 상태를 더 개선할 근거가 부족했습니다.'],
 no_robust_safe_gain: ['예방 보정을 추가하지 않았습니다.', '안전 조건을 만족하면서 통신 상태를 개선할 경로를 찾지 못했습니다.'],
 missing_rf_or_no_alternative: ['예방 보정을 추가하지 않았습니다.', '현재 관측이나 비행 조건에서 대체 경로를 검토할 수 없었습니다.'],
 no_recovery_action: ['복구 보정을 추가하지 않았습니다.', '현재 조건에서 선택할 복구 동작이 없었습니다.'],
 recovery_disabled: ['복구 보정을 추가하지 않았습니다.', '이 실행에서는 복구 보정이 꺼져 있습니다.']
};
const kv=(label,value)=>'<div class="detail-row"><span>'+esc(label)+'</span><strong>'+esc(value)+'</strong></div>';
const bool=v=>v===true?'예':v===false?'아니오':'미기록';
export function renderControlTrace(trace,time){
 if(!trace)return '<p class="hint">이 시각에는 추가 경로 보정에 관한 판단 기록이 없습니다.</p>';
 const reason = trace.reason ?? trace.decision_reason;
 const [outcome, explanation] = Object.hasOwn(reasons,reason)
  ? reasons[reason] : ['판단 결과를 확인할 수 없습니다.', reason ? '저장된 판단 코드 ' + reason : ''];
 const clock = finite(time) ? num(time,1) + '초' : '시각 미기록';
 return '<p class="inspector-context">' + esc(clock) + ' · 중계 편대의 판단</p>'
  + '<p class="trace-decision">' + esc(outcome) + '</p>'
  + (explanation ? '<p class="hint">' + esc(explanation) + '</p>' : '')
  + (finite(trace.early_score) ? kv('예방 점수',num(trace.early_score,4)) : '')
  + (finite(trace.threshold) ? kv('적용 기준',num(trace.threshold,4)) : '');
}
export function renderIntervention(event,selectedTime,allowFuture=false){
 const d={...(event.source_detail||{}),...event},start=d.start_s??d.time_s,actualAt=d.outcome_available_at_s??d.actual_available_at_s??d.interval_end_s??(finite(start)?start+3:null),ready=finite(actualAt)&&selectedTime>=actualAt;
 const label=d.reason==='prevention'?'예방':d.reason==='recovery'?'복구':d.reason||'미기록';
 const endpoint=Array.isArray(d.realized_endpoint)?'['+d.realized_endpoint.map(v=>num(v,1)).join(', ')+'] m':'미기록';
 return '<div class="intervention-detail">'+kv('추가 개입 분류',label)+(d.expert==='cut_recovery'?kv('판단 근거','수색 드론 비콘 관측 공백'):'')+(typeof d.actual_pre_action_connected==='boolean'?kv('판단 직전 수색망',d.actual_pre_action_connected?'연결':'단절'):'')+(typeof d.actual_search_connected_at_command==='boolean'?kv('명령 시각 수색망',d.actual_search_connected_at_command?'연결':'단절'):'')+
 (finite(d.early_score)?kv('혼합 예방 점수 / 기준',num(d.early_score,4)+' / '+(finite(d.threshold)?num(d.threshold,4):'미기록')):'')+
 (d.expert==='cut_recovery'&&Array.isArray(d.lost_beacon_age_s)?kv('소식이 끊긴 시간',d.lost_beacon_age_s.map(v=>num(v,1)+'초').join(', ')):'')+
 (d.expert==='cut_recovery'?kv('예상 재연결 대기', (finite(d.base_reconnect_in_s)?num(d.base_reconnect_in_s,1)+'초':'예측 범위 내 미연결')+' → '+(finite(d.projected_reconnect_in_s)?num(d.projected_reconnect_in_s,1)+'초':'예측 범위 내 미연결')):'')+
 (d.expert==='cut_recovery'&&finite(d.base_reconnect_deficit_db_s)&&finite(d.projected_reconnect_deficit_db_s)?kv('예상 통신 여유 부족 누적량',num(d.base_reconnect_deficit_db_s,2)+' → '+num(d.projected_reconnect_deficit_db_s,2)+' dB·s')+'<p class="hint">위치 불확실성을 반영한 모델 예측값이며 작을수록 좋습니다. 실제 단절 누적시간과 다릅니다.</p>':'')+
 kv('명령 수신',ready||allowFuture?bool(d.command_received):(finite(actualAt)?num(actualAt,1)+' s 이후 확인':'미기록'))+
 kv('요청대로 실행',ready||allowFuture?bool(d.executed_as_requested):(finite(actualAt)?num(actualAt,1)+' s 이후 확인':'미기록'))+
 kv('안전장치 차단',ready||allowFuture?bool(d.motion_guard_blocked):(finite(actualAt)?num(actualAt,1)+' s 이후 확인':'미기록'))+
 kv('실제 도달 위치',ready||allowFuture?endpoint:(finite(actualAt)?num(actualAt,1)+' s 이후 확인':'미기록'))+
 '<p class="hint">판단 직전 수색망은 명령 직전 마지막 0.5초 RF 구간 기록입니다. 명령 시각 수색망은 해당 시각에 시작하는 구간의 상태입니다. 예방·복구는 제어기의 판단 분류입니다. 복구 명령은 수색 드론의 비콘 관측 공백에서도 선택될 수 있어, 실제 수색망 단절과 항상 일치하지는 않습니다. 기본 정책을 바꾼 추가 개입이며, 연속 보정 구간 수와 다릅니다. 요청대로 실행됨과 실제 이동 거리는 별도로 확인합니다.'+(!ready&&allowFuture?' 현재 구간의 실행 결과는 사후 표시입니다.':'')+'</p></div>';
}
