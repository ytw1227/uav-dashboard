// The fleet classifier's onset probability and the controller's blended score are distinct outputs.
export const isTeam = manifest => manifest?.adapter_id === 'team-halfsec';
export const teamMarginSource = '팀 시뮬레이터가 기록한 구간별 SINR 여유입니다. 각 링크의 두 방향 중 낮은 SINR에서 기준을 뺀 뒤, 중계를 포함해 수색 드론 쌍이 확보할 수 있는 경로 여유의 최솟값을 사용합니다. 실제 연결 상태는 같은 구간의 양방향 링크 기록으로 판정합니다.';
export const teamColorRule = '기체 색은 현재 통신 그룹을 나타냅니다. 수색기 과반 그룹과 분리된 기체는 빨강입니다. 병목 링크의 양 끝 기체는 이번 기록에서 식별할 수 없습니다.';
export const teamEvaluationNote = '이 팀 버전의 기준 비행으로 별도 평가한 기록이 없어 선행 탐지율을 표시할 수 없습니다.';
const finite = Number.isFinite;
const number = n => n.toLocaleString('ko-KR', {maximumFractionDigits:4});

export function teamRisk(manifest, row) {
    const score = finite(row?.fleet_score) && row.support_count > 0 ? row.fleet_score : null;
    const threshold = finite(manifest.model_threshold) ? manifest.model_threshold : null;
    const time = row?.decision_time_s ?? null;
    const risk = {team:true, time, score, threshold, probability:null, probabilityText:'확률 미산출',
        probabilityDetail:'이 판단 시각에 편대 모델의 단절 예측값이 기록되지 않았습니다.',
        detail:'예방 판단에는 편대 모델 출력과 기존 예방 점수를 결합한 값을 사용합니다.',
        label:finite(score)&&finite(threshold)?(score>=threshold?'주의':'낮음'):'미평가'};
    if (!row || !finite(time)) return risk;
    const head = manifest.head_definitions?.fleet_onset_probability;
    if (head?.scope !== 'next_persistent_search_network_outage' || head.minimum_outage_s !== 1 ||
            head.window_bounds !== '[)' || head.window_s?.[0] !== 3 || head.window_s?.[1] !== 7) return risk;
    if (time <= 0 || row.pre_action_connected === false) {
        risk.probabilityDetail = '새 단절은 판단 직전 수색망이 연결된 경우에 예측합니다.';
        return risk;
    }
    if (time + 7 >= manifest.simulation_end_s) {
        risk.probabilityDetail = '비행 종료 전 7초 예측 구간이 온전히 확보되지 않았습니다.';
        return risk;
    }
    const p = row.fleet_onset_probability;
    if (row.pre_action_connected !== true || !(row.support_count > 0) || !finite(p) || p < 0 || p > 1) return risk;
    const percent = p * 100;
    let value = percent.toLocaleString('ko-KR', {maximumFractionDigits:percent < 1 || percent > 99 ? 2 : 1});
    if (percent > 0 && value === '0') value = '<0.01';
    if (percent < 100 && value === '100') value = '>99.99';
    risk.probability = p;
    risk.probabilityText = '모델 추정 ' + value + '%';
    risk.probabilityDetail = time + '초 판단에서 ' + (time + 3) + '초 이상 ' + (time + 7) +
        '초 미만에 1초 이상 지속될 새 수색망 단절이 시작될 모델 추정 확률입니다.';
    return risk;
}

export function scoreText(risk) {
    return finite(risk.score) ? '혼합 점수 ' + number(risk.score) : '점수 미기록';
}

export function teamDecisions(state) {
    const manifest=state.manifest, events=new Map();
    for(const event of state.events||[]) if(event.type==='intervention')events.set(event.start_s,event);
    const decisions=(state.overview.decisions||[]).map(row=>{
        const risk=teamRisk(manifest,row),event=events.get(risk.time),detail=event?.source_detail;
        return {time_s:risk.time,score:risk.score,threshold:risk.threshold,
            reason_label:event?'보정 명령 요청':'',intervention_kind:event?.reason,drone_id:detail?.changed_relay};
    });
    return {mode:manifest.mode,decision_dt_s:manifest.clocks.decision_dt_s,decisions,
        forecast_evaluation:{reason:teamEvaluationNote}};
}
