// The half-second controller logs its combined decision score and one network margin.
// Pair probabilities, bottleneck identifiers and separate forecast evaluation are absent.
export const isTeam = manifest => manifest?.adapter_id === 'team-halfsec';
export const teamMarginSource = '팀 시뮬레이터가 기록한 구간별 SINR 여유입니다. 각 링크의 두 방향 중 낮은 SINR에서 기준을 뺀 뒤, 중계를 포함해 수색 드론 쌍이 확보할 수 있는 경로 여유의 최솟값을 사용합니다. 실제 연결 상태는 같은 구간의 양방향 링크 기록으로 판정합니다.';
export const teamColorRule = '기체 색은 현재 통신 그룹을 나타냅니다. 수색기 과반 그룹과 분리된 기체는 빨강입니다. 병목 링크의 양 끝 기체는 이번 기록에서 식별할 수 없습니다.';
export const teamEvaluationNote = '이 팀 버전의 기준 비행으로 별도 평가한 기록이 없어 선행 탐지율을 표시할 수 없습니다.';
const scoreNote = '편대 모델과 기존 예방 점수를 결합한 제어 입력입니다. 이 비행의 기록에는 단절 발생 확률이 별도로 저장되어 있지 않습니다.';
const finite = Number.isFinite;
const number = n => n.toLocaleString('ko-KR', {maximumFractionDigits:4});

export function teamRisk(manifest, row) {
    const score = finite(row?.fleet_score) && row.support_count > 0 ? row.fleet_score : null;
    const threshold = finite(manifest.model_threshold) ? manifest.model_threshold : null;
    const time = row?.decision_time_s ?? null;
    return {team:true, time, score, threshold, probability:null, probabilityText:'확률 미기록',
        probabilityDetail:scoreNote, detail:finite(score)?'재배치 판단용 혼합 점수':'유효한 판단 점수 미기록',
        label:finite(score)&&finite(threshold)?(score>=threshold?'기준 이상':'기준 미만'):'미평가'};
}

export function scoreText(risk) {
    return finite(risk.score) ? '혼합 점수 ' + number(risk.score) : '점수 미기록';
}

function thresholdText(risk) {
    return finite(risk.threshold) ? '운영 기준 ' + number(risk.threshold) : '운영 기준 미기록';
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

export function mapScore(summary,risk) {
    const el=selector=>summary.querySelector(selector);
    el('.pe-risk-probability').textContent=scoreText(risk);
    const grade=el('.pe-current-grade');
    grade.hidden=!finite(risk.threshold)||!finite(risk.score);
    grade.textContent=grade.hidden?'':risk.label;
    el('.pe-forecast-window').hidden=true;
    const history=el('.pe-forecast-history');
    history.hidden=!finite(risk.time);
    if(history.hidden)return;
    el('.pe-history-title').textContent=risk.time+'초 판단 기록';
    el('.pe-risk-value').textContent=[scoreText(risk),thresholdText(risk)].join(' · ');
    el('.pe-history-note').textContent=scoreNote;
}

export function panelScore(el,risk,decision) {
    const score=el('fmScore');
    score.hidden=false;
    score.textContent=scoreText(risk);
    score.dataset.grade=String(finite(risk.score)&&finite(risk.threshold));
    score.dataset.risk=String(finite(risk.score)&&finite(risk.threshold)&&risk.score>=risk.threshold);
    score.dataset.outage='false';
    el('fmFleetProbability').hidden=true;
    el('fmDecisionTime').textContent=finite(risk.time)?risk.time+'초 판단':'';
    el('fmForecastWindow').textContent=score.dataset.grade==='true'?risk.label:'';
    el('fmScoreThreshold').textContent=thresholdText(risk);
    el('fmRecordedProbability').textContent='';
    el('fmForecastNote').textContent=scoreNote;
    el('fmDecision').textContent=decision?.reason_label||'';
    el('fmIntervention').textContent='';
    el('fmRecall').textContent=teamEvaluationNote;
    el('fmForecastPlot').hidden=false;
}
