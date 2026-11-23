import {droneMapLabel} from './map3d.js';
import {intervalAt} from './render.js';

const finite=Number.isFinite;
const rounded=(value,digits=1)=>Number(value.toFixed(digits)).toLocaleString('ko-KR',{maximumFractionDigits:digits});
const seconds=t=>rounded(t)+'초';
const unique=values=>[...new Set(values)];
const lastKnown=(rows,key,t)=>rows.findLast(row=>finite(row[key])&&row[key]<=t);

function view(kind,label,value,activeIds=[],detail=''){
    return {kind,label,value,activeIds:unique(activeIds),detail};
}

function lossFocus(c,t,alias){
    const e=c.event, frame=intervalAt(c.run.overview.rf_frames,t);
    const isActive=(a,b)=>frame?.links.some(edge=>edge.available&&
        ((edge.source===a&&edge.target===b)||(edge.source===b&&edge.target===a)));
    const edges=e.crossing_lost_links.filter(([a,b])=>!isActive(a,b));
    const ids=unique(edges.flat());
    const now=frame?.components||e.components_at_start;
    const isolated=now.filter(group=>group.length===1&&c.run.manifest.fleet.some(n=>n.id===group[0]&&n.role==='search')).flat();
    const groups=now.filter(group=>e.search_groups.some(search=>search.some(id=>group.includes(id)))).length;
    const state=isolated.length?isolated.map(alias).join('·')+' 고립':'수색망 '+groups+'개 그룹으로 분리';
    let value=state, detail='';
    if(edges.length)detail='끊긴 링크 '+edges.map(pair=>pair.map(alias).join('–')).join(', ');
    if(t===e.start_s&&finite(c.analysis?.margin_before_db)&&finite(c.analysis?.margin_at_onset_db))
        value+=' · 사건 분석 여유(근사) '+rounded(c.analysis.margin_before_db,2)+' → '+rounded(c.analysis.margin_at_onset_db,2)+' dB';
    return view('loss',e.brief?seconds(e.duration_s)+' 수색망 단절':'수색망 단절',value,isolated.concat(ids),detail);
}

function commandFocus(action,alias){
    const who=alias(action.relay);
    const label=action.reason==='recovery'?'복구 보정 선택':'예방 보정 선택';
    let value=who+' · 대형 목표 보정', detail='';
    if(action.expert==='cut_recovery'){
        value=who+' · 수색 드론 관측 공백에 대응';
        detail='예상 재연결 대기 '+(finite(action.base_reconnect_in_s)?seconds(action.base_reconnect_in_s):'예측 범위 내 미연결')+' → '+(finite(action.projected_reconnect_in_s)?seconds(action.projected_reconnect_in_s):'예측 범위 내 미연결');
        if(finite(action.base_reconnect_deficit_db_s)&&finite(action.projected_reconnect_deficit_db_s))detail+=' · 예상 통신 여유 부족 누적량 '+rounded(action.base_reconnect_deficit_db_s,2)+' → '+rounded(action.projected_reconnect_deficit_db_s,2)+' dB·s (작을수록 좋음 · 위치 불확실성 반영 예측)';
    }else if(finite(action.baseline_bad_s)&&finite(action.alternative_bad_s)){
        value=who+' · 예상 '+seconds(action.baseline_bad_s)+' → '+seconds(action.alternative_bad_s);
        detail='통신 여유가 0 dB 미만일 것으로 예상한 시간 · 원래 동작 → 대체 동작';
    }else if(finite(action.early_score)&&finite(action.threshold)){
        value=who+' · 예방 점수 '+rounded(action.early_score,4);
        detail='예방 기준 '+rounded(action.threshold,4);
    }
    return view('decision',label,value,[action.relay],detail);
}

function movementFocus(action,alias){
    const distance=action.moved_m;
    return view('movement','보정 뒤 이동 확인',
        alias(action.relay)+' · 실제 이동 '+rounded(distance)+' m',[action.relay],
        seconds(action.time_s)+' → '+seconds(action.outcome_available_s)+' 위치 기록');
}

function ordinaryFocus(d,nominal){
    const label=seconds(d.time_s)+' 판단';
    if(nominal)return view('decision',label,'기본 대형 비행 명령');
    if(finite(d.early_score)&&finite(d.threshold)){
        return view('decision',label,'예방 점수 '+rounded(d.early_score,4)+' / 기준 '+rounded(d.threshold,4),[],
            d.reason==='below_advance_threshold'?'추가 예방 보정 없음':'추가 개입 기록 없음');
    }
    return view('decision',label,'추가 개입 기록 없음');
}

/** Isolate the logged decision, motion or network state at the shared replay clock. */
export function incidentFocus(c,t,preferredKind){
    const e=c.event, alias=id=>droneMapLabel(id,c.run.manifest.fleet);
    if(t<e.start_s){
        const prior=lastKnown(e.prior_interventions||[],'time_s',t);
        if(prior&&t-prior.time_s<3)return view('before','단절 전 판단',alias(prior.relay)+' · '+(prior.reason==='recovery'?'복구':'예방')+' 보정 선택',[prior.relay],seconds(prior.time_s)+' 명령 기록');
        return view('before','단절 전 관측','단절 시작까지 '+seconds(e.start_s-t));
    }
    if(e.right_censored&&t>=e.end_s)return view('end','기록 종료','종료 시 미복구',[],seconds(e.start_s)+'부터 '+seconds(e.end_s-e.start_s)+'간 지속 단절');
    const action=lastKnown(e.interventions,'time_s',t);
    const moved=lastKnown(e.interventions.filter(a=>finite(a.moved_m)),'outcome_available_s',t);
    const decision=lastKnown(e.decisions,'time_s',t);
    const rec=e.recovery_s;
    const ended=finite(rec)&&t>=rec;
    const subsequent=ended&&(e.window_outages||[]).some(([start])=>start>=rec&&start<=t);
    if(subsequent)return view('end','재연결 이후',seconds(rec)+' 재연결');
    if(t===e.start_s&&preferredKind!=='decision'){
        const loss=lossFocus(c,t,alias);
        if(action?.time_s===t&&preferredKind!=='loss'){
            const cmd=commandFocus(action,alias);
            loss.detail+=' · 같은 시각 '+cmd.value;
            loss.activeIds=unique([...loss.activeIds,...cmd.activeIds]);
        }else if(!e.brief&&!e.decisions.length)loss.detail+=' · 단절 구간 내 새 제어 판단 없음';
        return loss;
    }
    if(moved&&t-moved.outcome_available_s<3&&(!ended||moved.outcome_available_s>rec||
        (moved.outcome_available_s===rec&&t===rec&&preferredKind==='movement'))){
        if(!action||action.time_s<=moved.outcome_available_s)return movementFocus(moved,alias);
    }
    if(ended){
        const paths=c.routes?.paths||[], ids=unique(paths.flat());
        const frame=intervalAt(c.run.overview.rf_frames,t);
        const present=(a,b)=>frame?.links.some(edge=>edge.available&&
            ((edge.source===a&&edge.target===b)||(edge.source===b&&edge.target===a)));
        const intact=paths.filter(path=>path.slice(1).every((id,i)=>present(path[i],id)));
        if(t>rec&&!intact.length)return view('end','재연결 이후',seconds(rec)+' 재연결');
        const shown=t===rec?paths:intact;
        const focus=view('recovery','수색망 재연결',shown.length?shown.map(path=>path.map(alias).join(' → ')).join(' / '):seconds(rec)+' 연결 회복',
            t===rec?ids:shown.flat(),seconds(e.duration_s)+' 단절 후 연결 회복');
        if(moved?.outcome_available_s===t&&t===rec&&preferredKind!=='recovery'){
            focus.detail+=' · '+movementFocus(moved,alias).value;
            focus.activeIds=unique([...focus.activeIds,moved.relay]);
        }
        return focus;
    }
    if(action&&(!decision||action.time_s>=decision.time_s))return commandFocus(action,alias);
    if(decision)return ordinaryFocus(decision,c.nominal);
    if(preferredKind==='decision')return view('decision','비행 명령 확인','해당 구간에 새 제어 판단 없음');
    const loss=lossFocus(c,t,alias);
    if(!e.brief&&!e.decisions.length)loss.detail+=' · 단절 구간 내 새 제어 판단 없음';
    return loss;
}
