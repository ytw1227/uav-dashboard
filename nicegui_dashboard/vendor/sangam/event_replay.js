import {finite,num,positionAt,intervalAt} from './render.js';

import {incidentFigure,updateIncidentMap,displayDrone,resetIncidentView,focusIncidentView,hasVisualScene} from './incident_map.js';
import {createIncidentRadio,recoveryRoutes} from './incident_radio.js';
import {renderDiagnosis} from './incident_story.js';
import {incidentFocus} from './incident_focus.js';
import {makeIncidentTimeline} from './incident_timeline.js';
import {loadIncidentSignals,incidentSignalWindow} from './incident_signals.js';

const byId = id => document.getElementById(id);
const stamp = t => finite(t) ? num(t,1)+'초' : '미기록';

const flightStep = 0.5;
const sceneOptions = {responsive:true,displaylogo:false,displayModeBar:false,scrollZoom:true};
const lastAt = (rows,t,key) => rows.findLast(row=>finite(row[key])&&row[key]<=t);
const vector = v => Array.isArray(v) ? '('+v.map(n=>num(n,1)).join(', ')+') m' : '미기록';
const decisionLabels = {
    added_cut_recovery:'수색 드론의 비콘 관측 공백에 대응해 재연결을 위한 복구 보정을 선택했습니다.',
    fleet_transformer_prevention:'편대 예측 점수와 안전·통신 개선 조건을 만족해 예방 보정을 선택했습니다.',
    below_advance_threshold:'예방 점수가 적용 기준 미만이어서 추가 예방 보정을 선택하지 않았습니다.',
    retained_v18:'기존 제어기의 보정 선택을 유지했습니다.',
    added_prevention:'단절 위험에 대비한 추가 예방 보정을 선택했습니다.',
    no_physical_prevention_evidence:'예방 보정을 검토할 통신 여유 조건을 충족하지 않았습니다.',
    no_robust_safe_gain:'안전 조건과 개선 기준을 모두 만족하는 대체 경로가 없었습니다.',
    missing_rf_or_no_alternative:'관측 또는 대체 경로가 부족했습니다.',
    recovery_disabled:'복구 보정이 꺼져 있었습니다.',
    no_recovery_action:'추가 복구 보정을 선택하지 않았습니다.',
    warmup:'결정을 위한 초기 관측을 수집 중이었습니다.',
    not_evaluated:'추가 판단을 수행하지 않았습니다.'
};
function option(value,label){
    const el=document.createElement('option');
    el.value=value;el.textContent=label;return el;
}
// Presentation priority only: this chronological evidence does not establish
// that the correction alone caused recovery. Require the recorded applied
// correction and completed displacement, not merely a requested command.
export function preferredIncident(events,mode){
    const ordered=[...events].sort((a,b)=>a.start_s-b.start_s||String(a.id).localeCompare(String(b.id)));
    const fallback={event:ordered[0]||null,kind:'first',intervention:null};
    if(mode!=='v21')return fallback;
    const nonzero=v=>Array.isArray(v)&&v.length>=2&&v.every(finite)&&Math.hypot(...v)>0;
    const candidates=[];
    for(const event of ordered){
        if(!finite(event.start_s)||!finite(event.recovery_s)||event.right_censored)continue;
        for(const action of event.interventions||[]){
            if(!finite(action.time_s)||!finite(action.outcome_available_s)||
               action.time_s<event.start_s||action.outcome_available_s<=action.time_s||
               action.outcome_available_s>event.recovery_s||
               !nonzero(action.applied_correction)||!nonzero(action.actual_delta)||
               !finite(action.moved_m)||action.moved_m<=0||
               action.executed_as_requested!==true||action.command_received!==true||
               action.motion_guard_blocked!==false)continue;
            const rank=action.reason==='recovery'&&action.actual_pre_action_connected===false?0:1;
            candidates.push({event,intervention:action,rank,kind:rank===0?'recovery':'correction'});
        }
    }
    candidates.sort((a,b)=>a.rank-b.rank||a.event.start_s-b.event.start_s||String(a.event.id).localeCompare(String(b.event.id))||a.intervention.time_s-b.intervention.time_s);
    return candidates[0]||fallback;
}
function samples(event,positions){
    const rows=[];
    for(let t=event.window[0];t<=event.window[1];t+=flightStep){
        const p=positionAt(positions,t);
        rows.push({time:t,p:new Map(p.uavs.map(v=>[v.id,v])),interpolated:p.interpolated});
    }
    return rows;
}
export function evidenceText(ctx,t){
    const shortId=id=>displayDrone(id,ctx.run.manifest.fleet);
    const e=ctx.event, parts=[], d=lastAt(e.decisions,t,'time_s'), a=lastAt(e.interventions,t,'time_s');
    if(t<e.start_s){
        const prior=lastAt(e.prior_interventions||[],t,'time_s');
        const info=prior?stamp(prior.time_s)+' '+shortId(prior.relay)+' '+(prior.reason==='recovery'?'복구':'예방')+' 보정 선택 · 사건 시작 전 개입 기록입니다.':'관측 구간 안에서 이 시각까지 추가 개입 기록이 없습니다.';
        return ['단절 전 관측', info, '아래 그래프에서 단절 전에 양방향으로 수신된 신호를 확인하세요.'];
    }
    if(!d&&!a){
        parts.push('선택한 단절 구간 안에서 현재 시각까지 기록된 새 제어 판단이 없습니다.');
    }else if(d){
        parts.push(stamp(d.time_s)+' 판단 · '+(ctx.nominal?'기본 대형 비행 명령을 사용했습니다.':decisionLabels[d.reason]||(d.reason?'기록된 판단 코드 · '+d.reason:'추가 판단 사유는 미기록입니다.')));
        if(!ctx.nominal&&finite(d.early_score)&&finite(d.threshold))parts.push('예방 점수 '+num(d.early_score,4)+' / 예방 기준 '+num(d.threshold,4));
        if(finite(d.missing_search_count)&&d.missing_search_count>0)parts.push('최근 3초 내 위치·경로 보고가 판단 중계기에 없는 수색 드론 '+d.missing_search_count+'대');
    }
    if(a){
        parts.push(stamp(a.time_s)+' '+shortId(a.relay)+' '+(a.reason==='recovery'?'복구':'예방')+' 개입 · 대형 목표에 요청한 보정 '+vector(a.command));
        if(a.expert==='cut_recovery'){
            const expected=value=>finite(value)?stamp(value):'예측 범위 내 미연결';
            parts.push('소식이 끊긴 수색기까지의 예상 재연결 대기 · 원래 동작 '+expected(a.base_reconnect_in_s)+' → 대체 동작 '+expected(a.projected_reconnect_in_s));
            if(finite(a.base_reconnect_deficit_db_s)&&finite(a.projected_reconnect_deficit_db_s))parts.push('예상 통신 여유 부족 누적량 · '+num(a.base_reconnect_deficit_db_s,2)+' → '+num(a.projected_reconnect_deficit_db_s,2)+' dB·s (위치 불확실성을 반영한 모델 예측값 · 작을수록 좋음)');
            if(finite(a.baseline_bad_s)&&finite(a.alternative_bad_s))parts.push('위치 불확실성을 반영한 예상 연결 여유가 0 dB 미만인 시간 · '+stamp(a.baseline_bad_s)+' → '+stamp(a.alternative_bad_s)+' (실제 수색망 단절 시간과 다릅니다)');
        }else if(finite(a.baseline_bad_s)&&finite(a.alternative_bad_s))parts.push('예상 통신 여유가 0 dB 미만인 시간 · 원래 동작 '+stamp(a.baseline_bad_s)+' → 대체 동작 '+stamp(a.alternative_bad_s));
        if(typeof a.actual_pre_action_connected==='boolean')parts.push('판단 직전 수색망 · '+(a.actual_pre_action_connected?'연결':'단절')+' (직전 마지막 0.5초 RF 구간)');
        if(typeof a.actual_search_connected_at_command==='boolean')parts.push('명령 시각 수색망 · '+(a.actual_search_connected_at_command?'연결':'단절')+' (명령 시각에 시작하는 RF 구간)');
    }
    const completedAction=lastAt(e.interventions.filter(v=>v.time_s<=t),t,'outcome_available_s');
    const movement=lastAt(ctx.movements,t,'outcome_available_s');
    if(completedAction){
        parts.push(stamp(completedAction.outcome_available_s)+' 확인 · '+shortId(completedAction.relay)+' 실제 변위 '+vector(completedAction.actual_delta)+' · 이동 '+num(completedAction.moved_m,1)+' m');
    }else if(movement&&finite(movement.outcome_available_s)&&movement.outcome_available_s<=t){
        parts.push(stamp(movement.outcome_available_s)+' 확인 · '+shortId(movement.relay)+' 실제 이동 '+num(movement.moved_m,1)+' m ('+stamp(movement.time_s)+'부터) · 적용 보정 '+vector(movement.applied_correction));
    }else if(a&&finite(a.outcome_available_s)){
        parts.push('이 개입 뒤 실제 변위는 '+stamp(a.outcome_available_s)+'에 기록됩니다.');
    }
    if(finite(e.recovery_s)&&e.recovery_s<=t){
        for(const path of ctx.routes.paths)parts.push('재연결 경로 · '+path.map(shortId).join(' → '));
    }else if(e.right_censored&&t>=e.end_s)parts.push('마지막 기록까지 재연결이 확인되지 않았습니다.');
    return ['선택한 단절 구간의 판단과 이동',...parts];
}
function stageRows(ctx){
    const e=ctx.event, action=e.interventions[0],decision=e.decisions[0];
    const shortId=id=>displayDrone(id,ctx.run.manifest.fleet);
    const groups=e.search_groups.map(g=>g.map(shortId).join('·')).join(' / ');
    const stages=[
        {kind:'before',t:e.window[0],label:'단절 전 상황',detail:e.left_censored?'기록이 단절 상태에서 시작했습니다.':'사건 직전의 드론 경로와 수신 신호'},
        {kind:'loss',t:e.start_s,label:'수색망 단절',detail:'분리된 수색 드론 그룹 '+groups},
        {kind:'decision',t:action?.time_s??decision?.time_s??e.start_s,label:action?'보정 명령 선택':'비행 명령 확인',detail:action?shortId(action.relay)+' '+(action.reason==='recovery'?'복구':'예방')+' 보정 선택':!decision?'기존 명령으로 비행 · 새 판단 시각 없음':ctx.nominal?'기본 대형의 비행 명령':'단절 중 추가 개입 기록 없음'},
        {kind:finite(e.recovery_s)?'recovery':'end',t:e.recovery_s??e.end_s,label:finite(e.recovery_s)?'수색망 재연결':'종료 시 미복구',detail:finite(e.recovery_s)?'가용 링크를 통해 수색 드론이 다시 연결':'실행 기록이 끝난 시점'}
    ];
    if(action&&finite(action.outcome_available_s)){
        stages.splice(3,0,{kind:'movement',t:action.outcome_available_s,label:'개입 뒤 이동 확인',detail:shortId(action.relay)+'의 실제 변위 기록'});
    }
    return stages.sort((a,b)=>a.t-b.t);
}

export function createIncidentReplay(onOpen){
    const launcher=byId('incidentLauncher'), dialog=byId('incidentDialog');
    const eventsBlock=byId('plotly-events'), figuresBlock=byId('plotly-figures');
    const diagnosisBlock=byId('incident-analysis');
    if(!launcher||!dialog||!window.Plotly)
        return {setRun(){},reset(){},open(){return false;},togglePlayback(){return false;},snapshot(){return {open:false,playing:false,initialized:false}}};
    const Plot=window.Plotly;
    let diagnoses=null,descriptor=null,signalCache=null;
    let loadVersion=0,caseVersion=0;
    let catalog=null, figures=null, run=null, book=null, ctx=null, timer=null;
    const quick=byId('incidentQuickSelect'), select=byId('incidentEvent'), slider=byId('incidentTime');
    const play=byId('incidentPlay'), back=byId('incidentBack'), forward=byId('incidentForward');
    const clock=byId('incidentClock'), error=byId('incidentError');
    const stages=byId('incidentStages'), evidence=byId('incidentEvidence');
    function stop(){
        clearInterval(timer);timer=null;play.textContent='▶ 재생';play.setAttribute('aria-pressed','false');
    }
    function dispose(){
        stop();
        globalThis.__UAV_PRESENTATION__?.resetIncident();
        const old=ctx;
        ctx=null;dialog.dataset.ready='false';
        if(old){
            // An async plot may still be attaching its WebGL scene.
            Promise.all([Promise.resolve(old.work).catch(()=>{}),Promise.resolve(old.map?.__visualDispose?.()).catch(()=>{})]).then(()=>{
                old.radio?.dispose();
                if(old.map?._fullLayout)Plot.purge(old.map);
            });
        }
    }
    function reset(){
        loadVersion++;caseVersion++;dispose();
        if(dialog.open)dialog.close();
        launcher.classList.add('hidden');book=null;run=null;figures=null;diagnoses=null;descriptor=null;signalCache=null;
    }
    async function setRun(state,region,title,mode){
        reset();const version=loadVersion;
        descriptor=globalThis.__DASHBOARD_ANALYSIS__?.incidents?.[state.runId]||null;
        if(descriptor?.revision!==state.revision&&!eventsBlock?.textContent)return;
        try{
            let entry,analysis;
            if(descriptor){
                if(descriptor.revision!==state.revision)return;
                [entry,analysis]=await Promise.all([globalThis.__DASHBOARD_READ_CHUNK__(descriptor.events_chunk),globalThis.__DASHBOARD_READ_CHUNK__(descriptor.analysis_chunk)]);
            }else{
                if(!catalog)catalog=JSON.parse(eventsBlock.textContent);
                entry=catalog[state.runId];analysis=JSON.parse(diagnosisBlock?.textContent||'{}')[state.runId];
            }
            if(version!==loadVersion||!entry||entry.revision!==state.revision)return;
            book=entry;diagnoses=analysis||{};run={region,title,mode,overview:state.overview,manifest:state.manifest,id:state.runId,revision:state.revision};
            const opts=entry.events.map((e,i)=>option(e.id,'사건 '+(i+1)+' · '+stamp(e.start_s)+' · '+(e.right_censored?'종료 시 미복구':stamp(e.duration_s)+' 지속')));
            quick.replaceChildren(...opts);select.replaceChildren(...opts.map(o=>o.cloneNode(true)));
            quick.disabled=!opts.length;byId('incidentOpen').disabled=!opts.length;
            if(!opts.length)quick.append(option('','1초 이상 지속된 수색망 단절 없음'));
            const example=document.querySelector('meta[name="incident-example"]');
            const exampleEvent=example?.dataset.run===state.runId && entry.events.find(e=>e.id===example.content);
            const preferred=exampleEvent?{event:exampleEvent,kind:'guide'}:preferredIncident(entry.events,mode);
            if(preferred.event){quick.value=preferred.event.id;select.value=preferred.event.id;}
            launcher.dataset.defaultEvent=preferred.event?.id||'';
            launcher.dataset.defaultKind=preferred.kind;
            quick.title=exampleEvent?'관찰 가이드의 대표 사건입니다. 다른 사건도 선택할 수 있습니다.':preferred.kind==='first'
                ?'기본 선택: 시간순 첫 사건. 보정·이동·재연결이 모두 확인되는 사례가 있으면 우선합니다.'
                :'기본 선택: 보정 적용과 실제 이동 뒤 재연결이 확인된 사건. 보정만으로 복구됐다는 인과 판정은 아닙니다.';
            launcher.classList.remove('hidden');
        }catch(error){
            if(version!==loadVersion)return;
            quick.replaceChildren(option('','사건 분석 자료를 열 수 없습니다.'));
            quick.disabled=true;byId('incidentOpen').disabled=true;launcher.classList.remove('hidden');
            launcher.title=error.message;
        }
    }
    function makePlot(host,id){
        const gd=document.createElement('div');gd.id=id;byId(host).replaceChildren(gd);return gd;
    }
    function message(value){
        error.textContent=value;error.hidden=!value;
    }
    function networkLabel(c,t){
        const ok=intervalAt(c.run.overview.rf_frames,t)?.metrics?.search_connected;
        const badge=byId('incidentNetwork');
        badge.textContent=ok===true||ok===1?'수색망 연결':ok===false||ok===0?'수색망 단절':'현재 RF 구간 기록 종료';
        badge.dataset.down=String(ok===false||ok===0);
    }
    function updateNarrative(c,t){
        const entries=c.stages;
        let latest=-1;
        entries.forEach((row,i)=>{if(t>=row.t&&(latest<0||row.t>=entries[latest].t))latest=i;});
        const kind=c.preferredKind||(c.mapView.relation==='selected'?c.mapView.focus.kind:null);
        const sameTime=entries.findIndex(row=>row.t===t&&row.kind===kind);
        if(sameTime>=0)latest=sameTime;
        entries.forEach((row,i)=>{
            const b=stages.children[i];
            b.setAttribute('aria-current',i===latest?'step':'false');
        });
        const [title,...items]=evidenceText(c,t);
        const head=document.createElement('strong');head.textContent=title;
        evidence.replaceChildren(head,...items.map(value=>{const p=document.createElement('p');p.textContent=value;return p;}));
        if(!hasVisualScene(c))networkLabel(c,t);
    }
    function attachMap(c){
        c.map.on('plotly_click',ev=>{
            const id=ev.points?.[0]?.customdata;
            if(ctx!==c||typeof id!=='string'||!c.run.manifest.fleet.some(node=>node.id===id))return;
            if(c.map.__visualAcceptPick&&!c.map.__visualAcceptPick(ev))return;
            stop();
            c.trackedId=c.trackedId===id?null:id;
            setTime(c.t,c.preferredKind);
        });
    }
    function mapLegend(c){
        const keys={lostLinks:['lost','끊긴 링크'],actualTrack:['moved','개입 뒤 실제 이동'],recoveryPath:['restored','재연결 경로'],trackedPath:['tracked','선택한 기체 궤적']};
        const items=[];
        for(const tr of c.map.data||[]){
            const info=keys[tr.meta?.role];
            if(!info||tr.visible===false||!tr.x?.some(Number.isFinite))continue;
            const el=document.createElement('span');el.className=info[0];el.textContent=info[1];items.push(el);
        }
        byId('incidentLineKey').replaceChildren(...items);
    }
    function renderCurrent(){
        const c=ctx;
        if(!c||!c.initialized||c.busy||(!hasVisualScene(c)&&c.map.__visualInteracting))return;
        c.busy=true;
        c.work=(async()=>{
            try{
                while(c.dirty&&ctx===c&&(hasVisualScene(c)||!c.map.__visualInteracting)){
                    c.dirty=false;
                    const t=c.t,mapView=c.mapView;
                    await Promise.all([updateIncidentMap(Plot,mapView,t),c.radio.setTime(t)]);
                    if(ctx===c&&(hasVisualScene(c)||!c.dirty)&&(hasVisualScene(c)||!c.map.__visualInteracting)){
                        mapLegend(c);
                        dialog.dataset.ready=String(!c.dirty);dialog.dataset.renderedCursor=String(t);
                        if(hasVisualScene(c)){clock.textContent=stamp(t);networkLabel(c,t);}
                        globalThis.__UAV_PRESENTATION__?.updateIncident({context:mapView,replayContext:c,time:t});
                    }
                }
            }catch(err){
                if(ctx===c){stop();message('사건 그림을 표시할 수 없습니다. '+(err?.message||''));}
            }finally{c.busy=false;}
        })();
    }
    function updateMapContext(c,t){
        const current=c.atTime(t);
        const mapView={...current,run:c.run,nominal:c.nominal,map:c.map,spec:c.spec,samples:c.samples,trackedId:c.trackedId};
        mapView.focus=incidentFocus(mapView,t,current.relation==='selected'?c.preferredKind:undefined);
        c.mapView=mapView;
        const {event,relation,focus}=mapView;
        const other=relation==='next'?'다음 단절':'이전 단절';
        const label=relation==='selected'?'선택 사건':other+(t===event.start_s?' 시작':'');
        byId('incidentFocusEvent').textContent=label+' · '+stamp(event.start_s);
        byId('incidentFocus').dataset.relation=relation;
        byId('incidentFocusLabel').textContent=focus.label;
        byId('incidentFocusValue').textContent=focus.value;
        byId('incidentFocusDetail').textContent=focus.detail;
        dialog.dataset.mapStart=String(event.start_s);
        dialog.dataset.mapKind=focus.kind;
    }
    function setTime(v,preferredKind){
        const c=ctx;if(!c||!finite(v))return;
        const e=c.event;
        c.t=Math.min(e.window[1],Math.max(e.window[0],Math.round(v/flightStep)*flightStep));
        c.preferredKind=preferredKind;
        slider.value=c.t;if(!hasVisualScene(c))clock.textContent=stamp(c.t);dialog.dataset.cursor=String(c.t);
        back.disabled=c.t<=e.window[0];forward.disabled=c.t>=e.window[1];
        updateMapContext(c,c.t);
        updateNarrative(c,c.t);
        byId('incidentTrackClear').hidden=!c.trackedId;
        dialog.dataset.ready='false';c.dirty=true;renderCurrent();
    }
    async function showCase(id){
        const e=book?.events.find(row=>row.id===id);
        if(!e||!run)return false;
        const version=++caseVersion,currentRun=run,currentBook=book,currentDescriptor=descriptor;
        dispose();message('3D 지도와 해당 구간의 통신 관측을 불러오는 중입니다.');
        for(const id of ['incidentMapHost','incidentRadioStack','incidentStages','incidentDiagnosis'])byId(id).replaceChildren();
        if(!dialog.open)dialog.showModal();
        byId('incidentFlight').textContent=run.title+' · '+(run.mode==='nominal'?'기본 대형 · 미적용':'예측·재배치 적용');
        const isCurrent=()=>version===caseVersion&&currentRun===run&&dialog.open;
        let spec,signals,signalTemplate;
        try{
            if(currentDescriptor){
                const windowKey=incidentSignalWindow(currentBook.events,e).join(':');
                const [map,template,observed]=await Promise.all([
                    globalThis.__DASHBOARD_READ_CHUNK__(currentDescriptor.map_chunk),
                    globalThis.__DASHBOARD_READ_CHUNK__(currentDescriptor.signal_template_chunk),
                    signalCache?.key===windowKey?signalCache.data:loadIncidentSignals(currentRun,currentBook,e,isCurrent)
                ]);
                if(!isCurrent()||!observed)return false;
                spec=map;signals=observed;signalTemplate=template;signalCache={key:windowKey,data:signals};
            }else{
                if(!figures)figures=JSON.parse(figuresBlock.textContent);
                spec=figures.maps[run.region];signals=figures.signals[run.id];signalTemplate=figures.signal;
            }
            if(!spec||!signals)throw Error('이 사건의 지도 또는 통신 기록이 없습니다.');
        }catch(error){if(isCurrent())message('사건 자료를 열 수 없습니다. '+error.message);return false;}
        if(!isCurrent())return false;
        message('');
        const c={run,book,event:e,frames:run.overview.rf_frames,analyses:diagnoses,signals,spec,nominal:run.mode==='nominal',
            samples:samples(e,run.overview.positions),map:makePlot('incidentMapHost','incidentMap'),
            dirty:true,initialized:false,analysis:diagnoses?.[id],trackedId:null,routes:recoveryRoutes(e,run.overview.rf_frames,run.manifest.fleet)};
        c.map.__visualResume=()=>{if(ctx===c&&!c.map.__visualDisposed){c.dirty=true;renderCurrent();}};
        c.atTime=makeIncidentTimeline(c);
        c.movements=[];
        for(const row of run.overview.decisions){
            if(row.decision_time_s>=e.end_s)break;
            const a=row.actions?.find(action=>action.uav_id===e.focus_relay);
            if(!a||!finite(a.actual_available_at_s)||a.actual_available_at_s<=e.start_s||!Array.isArray(a.actual_displacement))continue;
            c.movements.push({time_s:row.decision_time_s,relay:e.focus_relay,outcome_available_s:a.actual_available_at_s,
                moved_m:Math.hypot(...a.actual_displacement),applied_correction:a.applied_correction});
        }
        ctx=c;select.value=id;quick.value=id;
        if(!dialog.open)dialog.showModal();
        byId('incidentFlight').textContent=run.title+' · '+(c.nominal?'기본 대형 · 미적용':'예측·재배치 적용');
        dialog.dataset.event=id;dialog.dataset.start=String(e.start_s);dialog.dataset.end=String(e.end_s);
        slider.min=e.window[0];slider.max=e.window[1];
        renderDiagnosis(byId('incidentDiagnosis'),c);
        dialog.scrollTop=0;
        const radioSlot=document.createElement('div');
        byId('incidentRadioStack').replaceChildren(radioSlot);
        c.radio=createIncidentRadio(radioSlot,{event:e,signals,template:signalTemplate,frames:run.overview.rf_frames,
            fleet:run.manifest.fleet,analysis:c.analysis,routes:c.routes,seek:(when)=>{if(ctx===c){stop();setTime(when);}}});
        c.stages=stageRows(c);
        stages.replaceChildren(...c.stages.map((row,i)=>{
            const btn=document.createElement('button');btn.type='button';btn.className='incident-stage';
            btn.dataset.stage=i;btn.dataset.time=String(row.t);btn.dataset.kind=row.kind;
            const timeEl=document.createElement('time');timeEl.textContent=stamp(row.t);
            const text=document.createElement('span'),label=document.createElement('b'),detail=document.createElement('small');
            label.textContent=row.label;detail.textContent=row.detail;text.append(label,detail);
            btn.append(timeEl,text);btn.onclick=()=>{stop();setTime(row.t,row.kind);};return btn;
        }));
        setTime(e.start_s);
        const spatial=incidentFigure(c);
        const mapOptions=hasVisualScene(c)
            ?{...sceneOptions,responsive:false}:sceneOptions;
        const work=Promise.all([Plot.newPlot(c.map,spatial.data,spatial.layout,mapOptions),c.radio.ready]);
        c.work=work.then(async()=>{
            if(ctx!==c)return;
            await focusIncidentView(Plot,c);
            if(ctx!==c)return;
            attachMap(c);
            c.initialized=true;renderCurrent();
        }).catch(err=>{if(ctx===c)message('사건 그림을 표시할 수 없습니다. '+(err?.message||''));});
        return true;
    }
    function open(id){
        if(!book?.events.some(e=>e.id===id))return false;
        onOpen();void showCase(id);return true;
    }
    byId('incidentOpen').onclick=()=>open(quick.value);
    byId('incidentClose').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>{if(!dialog.open){caseVersion++;dispose();}});
    select.onchange=()=>showCase(select.value);
    byId('incidentFit').onclick=()=>{if(ctx)resetIncidentView(Plot,ctx);};
    byId('incidentTrackClear').onclick=()=>{if(ctx){ctx.trackedId=null;setTime(ctx.t,ctx.preferredKind);}};
    function step(direction){if(ctx){stop();setTime(ctx.t+direction*flightStep);}}
    back.onclick=()=>step(-1);
    forward.onclick=()=>step(1);
    slider.oninput=()=>{stop();setTime(Number(slider.value));};
    function togglePlayback(){
        if(!ctx||!ctx.initialized)return false;
        if(timer){stop();return true;}
        if(ctx.t>=ctx.event.window[1])setTime(ctx.event.window[0]);
        play.textContent='Ⅱ 일시정지';play.setAttribute('aria-pressed','true');
        let last=performance.now(),remaining=0;
        timer=setInterval(()=>{
            const c=ctx;if(!c){stop();return;}
            const now=performance.now();remaining+=(now-last)/1000*Number(byId('incidentSpeed').value);last=now;
            const steps=Math.floor(remaining/flightStep);
            if(!steps)return;
            remaining-=steps*flightStep;setTime(c.t+steps*flightStep);
            if(c.t>=c.event.window[1])stop();
        },75);
        return true;
    }
    play.onclick=togglePlayback;
    return {open,reset,setRun,togglePlayback,snapshot:()=>({open:dialog.open,playing:!!timer,initialized:!!ctx?.initialized,region:run?.region,eventId:ctx?.event?.id,time:ctx?.t,renderedTime:finite(Number(dialog.dataset.renderedCursor))?Number(dialog.dataset.renderedCursor):null})};
}


