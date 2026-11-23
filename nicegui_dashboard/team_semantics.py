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
            ("let marginReason = marginSource;", "let marginReason = isTeam(manifest)?teamMarginSource:marginSource;"),
            ("return {connected, margin, marginReason,", "return {team:isTeam(manifest), connected, margin, marginReason,"),
        ],
    ),
    "presentation.js": (
        "scoreText",
        [
            ("<title>가운데 선은 연결 기준 0 dB, 점은 현재 시각입니다.</title>",
             "<title>가운데 선은 '+(state.team?'SINR 기준':'연결 기준')+' 0 dB, 점은 현재 시각입니다.</title>"),
            ("const risk=state.risk, hasTime=",
             "summary.querySelector('.pe-network-title span').textContent=state.team?'SINR 기준 0 dB':'연결 기준 0 dB';"
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
             "const native=isTeam(s.manifest);colorRule.textContent=native?teamColorRule:archiveColorRule;"
             "el('fmRecallHelp').lastElementChild.hidden=native;"),
            ("el('fmScoreThreshold').textContent=[decisionTime,gate].filter(Boolean).join(' · ');",
             "const recordedGate=gate||(risk.team&&finite(risk.score)?scoreText(risk)+' · 기준 미기록':'');"
             "el('fmScoreThreshold').textContent=[decisionTime,recordedGate].filter(Boolean).join(' · ');"),
            ("const region=mapKey(state.manifest),descriptor=mapEvaluation(region);",
             "if(isTeam(state.manifest)){evaluation?.dispose();evaluation=null;evaluationRegion=null;"
             "el('fmEvaluationView').textContent=teamEvaluationNote;"
             "host.querySelector('.fm-heading p').textContent='선택한 팀 비행 버전의 평가 자료';"
             "host.querySelector('.fm-heading .eyebrow').textContent=mapName(state.manifest)+' · 예측 평가';return;}\n"
             "  const region=mapKey(state.manifest),descriptor=mapEvaluation(region);"),
            ("text:'연결 기준 0 dB'", "text:comm.team?'SINR 기준 0 dB':'연결 기준 0 dB'"),
            ("el('fmMetricNote').textContent=d.note;\n }else{",
             "el('fmMetricNote').textContent=comm.team?'가로 점선은 SINR 기준 0 dB, 베이지색은 실제 단절 구간입니다.':d.note;\n }else{"),
            ("el('fmMetricValue').textContent=finite(val)?num(val*d.scale,2)+d.unit:'첫 집계 전';",
             "el('fmMetricValue').textContent=finite(val)?num(val*d.scale,2)+d.unit:"
             "comm.team&&metric==='information_shared_fraction'?'공통 공유율 미기록':'첫 집계 전';"),
            ("el('fmMetricNote').textContent=d.note;\n  trace={type:'scatter',mode:'lines',",
             "el('fmMetricNote').textContent=comm.team&&metric==='information_shared_fraction'?"
             "'이번 비행은 공통 공유율 시계열을 기록하지 않았습니다.':d.note;\n  trace={type:'scatter',mode:'lines',"),
        ],
    ),
    "app.js": (
        "isTeam,teamRisk,scoreText",
        [
            ("function renderLinkPredictions(decision, role, final){",
             "function renderLinkPredictions(decision, role, final){\n if(isTeam(S.manifest))"
             "return '<p class=\"hint\">이 팀 비행에서는 드론 쌍별 단절 확률을 기록하지 않았습니다.</p>';"),
            ("$('controlTraceDetail').innerHTML = final && !nominal ?",
             "$('controlTraceDetail').innerHTML = isTeam(S.manifest) ? "
             "kv('재배치 판단 점수',scoreText(teamRisk(S.manifest,"
             "S.time<S.duration?a.decision:null))) : final && !nominal ?"),
        ],
    ),
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
    return f"import {{{imports}}} from './team_readouts.js';\n" + code
