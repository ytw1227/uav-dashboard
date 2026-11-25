"""Apply the sealed team-flight display semantics to compatible regional player modules."""

from pathlib import PurePosixPath


HOOKS = {
    "comm_state.js": (
        "isTeam,teamRisk,teamMarginSource",
        [
            ("if (!row || !finite(dt)",
             "if(isTeam(manifest))return teamRisk(manifest,row && finite(dt) && time < row.decision_time_s + dt "
             "&& time < manifest.simulation_end_s ? row : null);\n    if (!row || !finite(dt)"),
            ("const margin = finite(value)", "const margin = isTeam(manifest) ? (finite(value)?value:null) : finite(value)"),
            ("let marginReason = marginSource;", "let marginReason = isTeam(manifest)?(record?.exact?"
             "'현재 비행의 실제 SINR과 90% 양방향 DATA·ACK 연결 기준으로 각 경로의 통신 여유를 구했고 기록된 링크와 대조했습니다.':teamMarginSource):marginSource;"),
            ("return {connected, margin, marginReason,", "return {team:isTeam(manifest), exactMargin:record?.exact===true, connected, margin, marginReason,"),
        ],
    ),
    "presentation.js": (
        "scoreText",
        [
            ("<title>가운데 선은 연결 기준 0 dB, 점은 현재 시각입니다.</title>",
             "<title>가운데 선은 '+(state.team&&!state.exactMargin?'SINR 기준':'연결 기준')+' 0 dB, 점은 현재 시각입니다.</title>"),
            ("const risk=state.risk, hasTime=",
             "summary.querySelector('.pe-network-title span').textContent=state.team&&!state.exactMargin?'SINR 기준 0 dB':'연결 기준 0 dB';"
             "\n const risk=state.risk, hasTime="),
            (":'예방 판단 점수 미기록';", ":risk.team && finite(risk.score)?scoreText(risk)+' · 기준 미기록':'예방 판단 점수 미기록';"),
        ],
    ),
    "flight_metrics.js": (
        "isTeam,teamDecisions,teamColorRule,teamEvaluationNote,scoreText",
        [
            ("export function fromRecordedDecisions(state){", "export function fromRecordedDecisions(state){\n"
             " if(isTeam(state.manifest))return teamDecisions(state);"),
            ("el('fmNodeStates').before(colorRule);",
             "const archiveColorRule=colorRule.textContent;el('fmNodeStates').before(colorRule);"),
            ("if(key!==runKey){runKey=key;", "if(key!==runKey){runKey=key;"
             "const native=isTeam(s.manifest);colorRule.textContent=native&&!s.manifest.team_telemetry?.exact_margin?teamColorRule:archiveColorRule;"
             "el('fmRecallHelp').lastElementChild.hidden=native&&!mapEvaluation(mapKey(s.manifest))?.chunk;"),
            ("el('fmScoreThreshold').textContent=[decisionTime,gate].filter(Boolean).join(' · ');",
             "const recordedGate=gate||(risk.team&&finite(risk.score)?scoreText(risk)+' · 기준 미기록':'');"
             "el('fmScoreThreshold').textContent=[decisionTime,recordedGate].filter(Boolean).join(' · ');"),
            ("const region=mapKey(state.manifest),descriptor=mapEvaluation(region);",
             "const region=mapKey(state.manifest),descriptor=mapEvaluation(region),evaluationKey=descriptor?.chunk||region;"
             "if(isTeam(state.manifest)&&!descriptor?.chunk?.startsWith('team-evaluation/')){evaluation?.dispose();evaluation=null;evaluationRegion=null;"
             "el('fmEvaluationView').textContent=teamEvaluationNote;"
             "host.querySelector('.fm-heading p').textContent='선택한 팀 비행 버전의 평가 자료';"
             "host.querySelector('.fm-heading .eyebrow').textContent=mapName(state.manifest)+' · 예측 평가';return;}"),
            ("!evaluation||evaluationRegion!==region", "!evaluation||evaluationRegion!==evaluationKey"),
            ("evaluationRegion=region;", "evaluationRegion=evaluationKey;"),
            ("text:'연결 기준 0 dB'", "text:comm.team&&!comm.exactMargin?'SINR 기준 0 dB':'연결 기준 0 dB'"),
            ("el('fmMetricNote').textContent=d.note;\n }else{",
             "el('fmMetricNote').textContent=comm.team&&!comm.exactMargin?'가로 점선은 SINR 기준 0 dB, 베이지색은 실제 단절 구간입니다.':d.note;\n }else{"),
            ("el('fmMetricValue').textContent=finite(val)?num(val*d.scale,2)+d.unit:'첫 집계 전';",
             "el('fmMetricValue').textContent=finite(val)?num(val*d.scale,2)+d.unit:"
             "comm.team&&metric==='information_shared_fraction'&&!state.manifest?.team_telemetry?.shared_series?'공통 공유율 미기록':'첫 집계 전';"),
            ("el('fmMetricNote').textContent=d.note;\n  trace={type:'scatter',mode:'lines',",
             "el('fmMetricNote').textContent=comm.team&&metric==='information_shared_fraction'&&!state.manifest?.team_telemetry?.shared_series?"
             "'이번 비행은 공통 공유율 시계열을 기록하지 않았습니다.':d.note;\n  trace={type:'scatter',mode:'lines',"),
        ],
    ),
    "app.js": (
        "isTeam,teamRisk,scoreText,pairHeads",
        [
            ("function renderLinkPredictions(decision, role, final){",
             "function renderLinkPredictions(decision, role, final){\n if(isTeam(S.manifest)&&!S.manifest.head_definitions?.next_onset_lt5s)"
             "return '<p class=\"hint\">이 팀 비행에서는 드론 쌍별 단절 확률을 기록하지 않았습니다.</p>';"),
            ("const predictions = (decision?.predictions || []).filter", "const predictions = (isTeam(S.manifest)?"
             "pairHeads(S.manifest,decision,S.node,S.peer):(decision?.predictions || [])).filter"),
            ("' · 11월 13일 단절 시점 예측'", "(isTeam(S.manifest)?' · 현재 팀 모델 단절 시점 예측':' · 11월 13일 단절 시점 예측')"),
            ("$('controlTraceDetail').innerHTML = final && !nominal ?",
             "$('controlTraceDetail').innerHTML = isTeam(S.manifest) ? "
             "kv('재배치 판단 점수',scoreText(teamRisk(S.manifest,"
             "S.time<S.duration?a.decision:null))) : final && !nominal ?"),
            ("if(e.type==='intervention')return", "if(e.type==='applied_command')return '보정 명령 적용';if(e.type==='intervention')return"),
        ],
    ),
    "final_v1.js": ("teamIntervention", [
        ("export function renderIntervention(event,selectedTime,allowFuture=false){",
         "export function renderIntervention(event,selectedTime,allowFuture=false){\n"
         " if(event.team_receipt_clock)return teamIntervention(event,selectedTime,allowFuture,kv);")
    ]),
    "forecast_evaluation.js": ("", [
        ("const selected=selectedEvent(),rows=shownEvents(),position=",
         "if(metadata.reference_checkpoint_sha256){el('feConditions').textContent+=' · 기준 제어기 0회차 · 적용 모델 '+metadata.evaluated_step+'회차';"
         "if(metadata.model_sha256!==metadata.report_checkpoint_sha256)el('feConditions').textContent+=' (보고서 전체 평가 모델 '+metadata.report_step+'회차)';}"
         "\n  const selected=selectedEvent(),rows=shownEvents(),position="),
    ]),
    "incident_story.js": ("", [
        ("host.append(title);", "host.append(title);if(a.evidence_note)host.append(node('p',a.evidence_note,'diagnosis-finding'));")
    ]),
    "event_replay.js": ("", [
        ("const decisionLabels = {","const decisionLabels = {team_recovery:'팀 제어기가 복구 보정을 선택했습니다.',"),
        ("action.executed_as_requested!==true||action.command_received!==true||\n               action.motion_guard_blocked!==false",
         "action.command_received!==true||(action.offset_receipt_and_motion!==true&&"
         "(action.executed_as_requested!==true||action.motion_guard_blocked!==false))")
    ]),
}


def adapt(name, code):
    hook = HOOKS.get(PurePosixPath(name.replace('\\', '/')).name)
    if not hook:
        return code
    imports, replacements = hook
    for original, replacement in replacements:
        if code.count(original) != 1:
            raise ValueError(f"재생 화면에서 팀 비행 표시 위치를 식별할 수 없습니다 ({name}, {original!r}).")
        code = code.replace(original, replacement)
    return (f"import {{{imports}}} from './team_readouts.js';\n" if imports else "") + code
