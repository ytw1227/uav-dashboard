import {commAt,formatMargin,warningRuleText} from './comm_state.js';
import {esc,finite,num,before,intervalAt,packetAt} from './render.js';
import {displayDrone} from './incident_map.js';
import {createForecastEvaluation} from './forecast_evaluation.js';
import {createFlightRadio} from './flight_radio.js';
import {mapName,mapKey,mapEvaluation} from './library.js';

const METRICS={
 network_margin:{label:'전체 수색망 통신 여유 (dB)',unit:'dB',scale:1,note:'0 dB는 연결 기준입니다. 베이지색은 실제 수색망 단절 구간입니다.'},
 pdr_so_far:{label:'누적 패킷 전달률 · PDR',unit:'%',scale:100,note:'시작부터 현재 집계까지의 패킷 전달률입니다. 0.25초 적시 전달률과 다르며, 비행 종료 정산값과도 차이가 날 수 있습니다.'},
 information_shared_fraction:{label:'공통 공유율',unit:'%',scale:100,note:'원본에 기록된 공통 공유율입니다. 현재 완료된 집계값을 표시합니다.'}
};
const CONFIG={responsive:true,displayModeBar:false,scrollZoom:false};
function json(id){return JSON.parse(document.getElementById(id)?.textContent||'{}')}
function outages(rows){const spans=[];for(const r of rows){if(r.metrics?.search_connected!==0&&r.metrics?.search_connected!==false)continue;const a=r.interval_start_s,b=r.interval_end_s,last=spans.at(-1);if(last&&Math.abs(last[1]-a)<1e-8)last[1]=b;else spans.push([a,b])}return spans}
function atGraph(rows,t){return intervalAt(rows,t)}
function shapes(spans,t){return [{type:'line',xref:'x',yref:'paper',x0:t,x1:t,y0:0,y1:1,line:{color:'#183e56',width:2,dash:'dot'}},...spans.map(([a,b])=>({type:'rect',xref:'x',yref:'paper',x0:a,x1:b,y0:0,y1:1,line:{width:0},fillcolor:'rgba(214,163,102,.20)',layer:'below'}))]}

export function fromRecordedDecisions(state){
 const mode=state.manifest.mode??state.manifest.metadata?.mode;
 const reasonLabels={added_cut_recovery:'수색 드론 관측 공백에 대한 복구 보정 선택',fleet_transformer_prevention:'편대 예측에 따른 예방 보정 선택',below_advance_threshold:'예방 판단 기준 미달',retained_v18:'기존 예방·복구 보정 우선 적용',missing_rf_or_no_alternative:'관측 부족·임무 종료·선택 가능한 대안 없음 중 하나',added_prevention:'추가 예방 보정 선택',no_robust_safe_gain:'안전하게 개선할 보정안 없음',no_physical_prevention_evidence:'물리적으로 예방할 근거 부족',not_evaluated:'예방 점수 평가 전',warmup:'초기 관측 수집 중'};
 const interventions=(state.events||[]).filter(e=>e.type==='intervention');
 const decisions=(state.overview.decisions||[]).filter(row=>row.control_trace).map(row=>{
  const trace=row.control_trace,when=row.decision_time_s;
  const event=interventions.find(e=>e.start_s===when),detail=event?{...(event.source_detail||{}),...event}:null;
  const reason=trace.reason??trace.decision_reason;
  let label=reasonLabels[reason]||'기록된 판단: '+(reason||'미기록');
  if(reason==='retained_v18'&&detail)label=detail.reason==='recovery'?'기존 복구 보정 우선 적용':'기존 예방 보정 우선 적용';
  return {time_s:when,score:finite(trace.early_score)?trace.early_score:null,threshold:finite(trace.threshold)?trace.threshold:null,
   recorded:finite(trace.early_score),reason,reason_label:label,intervention_kind:detail?.reason??null,
   drone_id:detail?.changed_relay??null,executed_as_requested:typeof detail?.executed_as_requested==='boolean'?detail.executed_as_requested:null,
   outcome_available_at_s:detail?.outcome_available_at_s??detail?.actual_available_at_s??detail?.interval_end_s??null};
 });
 return {run_id:state.runId,bundle_revision:state.revision,mode,decision_dt_s:3,decisions,
  recorded_prevention:{threshold:decisions.find(d=>finite(d.threshold))?.threshold??null},
  forecast_evaluation:{reason:mode==='nominal'?'기본 대형 비행은 비행 중 예측·재배치를 사용하지 않습니다.':'현재 제어 비행의 예방 판단 기록입니다. 선행 탐지율은 별도의 기준 비행에서 평가합니다.'}};
}

export function createFlightMetrics(host,{Plot,seek,setPair,fetchObservations,onEvaluationOpen}){
 const figures=json('flight-figures'),evaluationData=json('bundang-evaluation');
 let state=null,runKey='',view='fleet',metric='network_margin',rangeMode='local',spans=[],radio=null,evaluation=null,stamp='',plotKey='',forecastKey='',drawing=false,pending=false;
 let evaluationRegion=null,recordedForecast=null;
 const plotBusy=new Map();
 host.innerHTML=`<div class="fm-heading"><div><span class="eyebrow">재생 시각에 맞춘 분석</span><h3>통신 상태와 판단 흐름</h3><p>전체 통신의 변화부터 살펴보고, 필요한 드론 쌍의 신호를 확인하세요.</p></div><strong id="fmTime" class="fm-time">0.0초</strong></div>
 <div class="fm-stats"><div><span>전체 수색망 통신 여유</span><strong id="fmMargin">—</strong><small>필수 수색 연결의 기준 대비 여유</small></div><div><span>현재 수색망</span><strong id="fmNetwork">—</strong><small id="fmGraphTime"></small></div><div><span>도달 가능한 수색 드론 쌍</span><strong id="fmPairs">—</strong><small>다른 기체를 경유하는 경로 포함</small></div></div>
 <div class="fm-overview"><div class="fm-row"><strong>전체 비행의 수색망 연결 상태</strong><span><i class="fm-swatch fm-good"></i>수색망 연결 <i class="fm-swatch fm-bad"></i>수색망 단절</span></div><svg id="fmTimeline" viewBox="0 0 1000 54" preserveAspectRatio="none" role="slider" tabindex="0" aria-label="전체 비행의 수색망 연결 상태 · 시각 선택" aria-valuemin="0"></svg><div class="fm-row fm-muted"><span>띠를 눌러 해당 시각으로 이동하세요.</span><span id="fmDuration"></span></div></div>
 <div class="fm-navigation"><div class="fm-tabs" role="tablist" aria-label="통신 분석 범위"><button id="fmFleet" role="tab" aria-selected="true">전체 통신</button><button id="fmRadioTab" role="tab" aria-selected="false">연결 상태·신호</button><button id="fmEvaluationTab" role="tab" aria-selected="false">예측 성능 평가</button></div><label>시간 범위<select id="fmRange"><option value="local">현재 시각 ±15초</option><option value="full">전체 비행</option></select></label></div>
 <div id="fmFleetView" role="tabpanel"><div class="fm-chart-head"><label>시간별 지표<select id="fmMetric">${Object.entries(METRICS).map(([k,v])=>`<option value="${k}">${v.label}</option>`).join('')}</select></label><div id="fmPacketReadout"><span>현재 집계</span><strong id="fmMetricValue">—</strong></div></div><div id="fmPlot" class="fm-plot" aria-label="전체 통신 지표 그래프"></div><div class="fm-legend"><span><i class="fm-swatch fm-shade"></i>수색망 단절 · 짧은 단절 포함</span><span>점선: 재생 시각 · 그래프 클릭으로 이동</span></div><p id="fmMetricNote" class="fm-note"></p>
 <details id="fmMarginBasis" class="fm-evaluation"><summary>통신 여유 산출 근거</summary><p id="fmMarginSource"></p><p id="fmMarginRoute"></p></details>
 <section id="fmForecast" class="fm-forecast"><div class="fm-row"><h4>새 수색망 단절 예측</h4><span id="fmDecisionTime" class="fm-muted"></span></div><div class="fm-risk"><strong id="fmFleetProbability" hidden></strong><strong id="fmScore">—</strong><span id="fmForecastWindow"></span></div><details id="fmRiskBasis" class="fm-evaluation"><summary>판단 근거와 점수 기록</summary><p id="fmScoreThreshold"></p><p id="fmRecordedProbability"></p><p id="fmForecastNote"></p><div class="fm-decision-text"><strong id="fmDecision"></strong><span id="fmIntervention"></span></div><div id="fmForecastPlot" class="fm-forecast-plot" aria-label="편대 위험 판단 점수 그래프"></div></details><details class="fm-evaluation" id="fmRecallHelp"><summary>현재 재생과 예측 평가의 차이</summary><p id="fmRecall"></p><p>선행 탐지율은 「예측 성능 평가」 탭의 별도 기준 비행에서 확인할 수 있습니다.</p></details></section></div>
 <div id="fmEvaluationView" role="tabpanel" hidden></div><div id="fmRadioView" role="tabpanel" hidden><div class="fm-pair-controls"><label>드론 A<select id="fmNode"></select></label><span>↔</span><label>드론 B<select id="fmPeer"></select></label></div><div id="fmRadioHost"></div><section id="fmReach" class="fm-reach"><h4>중계 경로를 포함한 연결 상태</h4><div class="fm-reach-cards"><div><span>선택한 두 드론</span><strong id="fmRouteStatus"></strong><small id="fmRoutePath"></small></div><div><span>전체 수색망</span><strong id="fmReachNetwork"></strong><small id="fmGroups"></small></div></div><details class="fm-evaluation" open><summary>기체별 연결 상태</summary><div id="fmNodeStates"></div></details></section></div>`;
 const el=id=>host.querySelector('#'+id);
 const colorRule=document.createElement('p');
 colorRule.className='fm-color-rule';
 colorRule.textContent=warningRuleText()+' 빨강은 수색기 과반이 모인 그룹에 도달할 수 없는 기체를 뜻합니다. 과반 그룹이 없으면 모두 빨강으로 표시합니다.';
 el('fmNodeStates').before(colorRule);
 el('fmRiskBasis').ontoggle=()=>{if(state&&view==='fleet')draw(true)};
 el('fmNodeStates').addEventListener('click',event=>{
  const id=event.target.closest('[data-inspect]')?.dataset.inspect;
  if(!id||!state)return;
  setPair(id,id===state.peer?state.node:state.peer);
 });
 const resize=new ResizeObserver(()=>{if(!state||host.hidden)return;for(const id of ['fmPlot','fmForecastPlot']){const div=el(id);if(div._fullLayout&&div.getBoundingClientRect().width>0&&!div.hidden)Plot.Plots.resize(div)}});resize.observe(host);
 function windowRange(){if(rangeMode==='full')return [0,state.duration];return [Math.max(0,state.time-15),Math.min(state.duration,state.time+15)]}
 function seekPlot(div,e){const axis=div._fullLayout?.xaxis;if(!axis||e.target.closest('.modebar'))return;const box=div.getBoundingClientRect(),x=e.clientX-box.left-axis._offset,y=e.clientY-box.top,ya=div._fullLayout.yaxis;if(x<0||x>axis._length||y<ya._offset||y>ya._offset+ya._length)return;seek(Math.round(axis.p2d(x)*2)/2)}
 for(const id of ['fmPlot','fmForecastPlot'])el(id).addEventListener('click',e=>seekPlot(el(id),e));
 el('fmTimeline').onclick=e=>{const rect=e.currentTarget.getBoundingClientRect(),ratio=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));seek(Math.round(ratio*state.duration*2)/2)};
 el('fmTimeline').onkeydown=e=>{if(!state)return;let t;if(e.key==='ArrowLeft')t=state.time-.5;if(e.key==='ArrowRight')t=state.time+.5;if(e.key==='Home')t=0;if(e.key==='End')t=state.duration;if(t!==undefined){e.preventDefault();seek(t)}};
 el('fmMetric').onchange=e=>{metric=e.target.value;plotKey='';draw(true)};
 el('fmRange').onchange=e=>{rangeMode=e.target.value;draw(true)};
 function viewVisibility(){const isEvaluation=view==='evaluation';el('fmFleetView').hidden=view!=='fleet';el('fmRadioView').hidden=view!=='radio';el('fmEvaluationView').hidden=!isEvaluation;el('fmFleet').setAttribute('aria-selected',String(view==='fleet'));el('fmRadioTab').setAttribute('aria-selected',String(view==='radio'));el('fmEvaluationTab').setAttribute('aria-selected',String(isEvaluation));for(const target of [host.querySelector('.fm-stats'),host.querySelector('.fm-overview'),el('fmRange').parentElement,el('fmTime')])target.hidden=isEvaluation;host.querySelector('.fm-heading .eyebrow').textContent=mapName(state?.manifest)+(isEvaluation?' · 보고서 방식의 추가 평가':' · 재생 시각에 맞춘 분석');host.querySelector('.fm-heading h3').textContent=isEvaluation?'예측 성능과 사건별 결과':'통신 상태와 판단 흐름';host.querySelector('.fm-heading p').textContent=isEvaluation?'기존 보고서 평가 함수로 계산한 기준 비행의 결과입니다.':'전체 통신의 변화부터 살펴보고, 필요한 드론 쌍의 신호를 확인하세요.';}
 function setView(value){view=value;viewVisibility();if(view==='evaluation')onEvaluationOpen?.();draw(true)}
 el('fmFleet').onclick=()=>setView('fleet');el('fmRadioTab').onclick=()=>setView('radio');el('fmEvaluationTab').onclick=()=>setView('evaluation');
 for(const id of ['fmNode','fmPeer'])el(id).onchange=()=>{let a=el('fmNode').value,b=el('fmPeer').value;if(a===b)b=state.manifest.fleet.find(n=>n.id!==a)?.id;setPair(a,b)};
 function updatePair(){const fleet=state.manifest.fleet||[],html=fleet.map(n=>`<option value="${esc(n.id)}">${esc(displayDrone(n.id,fleet))} · ${n.role==='search'?'수색':'중계'}</option>`).join('');if(el('fmNode').dataset.run!==runKey){el('fmNode').innerHTML=html;el('fmPeer').innerHTML=html;el('fmNode').dataset.run=runKey}el('fmNode').value=state.node;el('fmPeer').value=state.peer;for(const op of el('fmPeer').options)op.disabled=op.value===state.node}
 function snapshot(knownComm){if(!state)return null;const rows=state.overview.rf_frames,graph=atGraph(rows,state.time,state.duration),packet=packetAt(rows,state.time),n=(state.manifest.fleet||[]).filter(n=>n.role==='search').length,total=n*(n-1)/2,ratio=graph?.metrics?.search_pair_reachability;const comm=knownComm||commAt(state,graph,state.time,state.node,state.peer);return {margin:comm.margin,risk:comm.risk,route:comm.route,runId:state.runId,revision:state.revision,time:state.time,graphStart:graph?.interval_start_s??null,packetEnd:packet?.interval_end_s??null,connected:graph?.metrics?.search_connected??null,reachablePairs:finite(ratio)?Math.round(ratio*total):null,totalPairs:total,pdr:packet?.metrics?.pdr_so_far??null,shared:packet?.metrics?.information_shared_fraction??null,range:windowRange(),metric,view,spans:spans.map(s=>[...s])}}
 function timeline(){const X=t=>t/state.duration*1000,[a,b]=windowRange();el('fmTimeline').innerHTML=`<rect x="0" y="15" width="1000" height="18" rx="3" fill="#70afa4"/>${spans.map(([s,e])=>`<rect x="${X(s)}" y="15" width="${Math.max(.35,X(e-s))}" height="18" fill="#bd5450"><title>${s}–${e}초 수색망 단절</title></rect>`).join('')}<rect x="${X(a)}" y="9" width="${Math.max(1,X(b-a))}" height="30" fill="none" stroke="#163f56" stroke-width="1.5"/><path d="M${X(state.time)} 4V44" stroke="#163f56" stroke-width="3"/><circle cx="${X(state.time)}" cy="6" r="4" fill="#163f56"/>`;el('fmTimeline').setAttribute('aria-valuenow',state.time);el('fmTimeline').setAttribute('aria-valuemax',state.duration);el('fmDuration').textContent=`0초 – ${num(state.duration,0)}초`}
 async function updatePlot(div,data,layout,key,previous){if(plotBusy.get(div)){pending=true;return previous}plotBusy.set(div,true);try{if(previous!==key||!div._fullLayout){await Plot.react(div,data,layout,CONFIG);return key}await Plot.relayout(div,{'xaxis.range':layout.xaxis.range,'shapes[0].x0':layout.shapes[0].x0,'shapes[0].x1':layout.shapes[0].x1,annotations:layout.annotations||[]});return previous}finally{plotBusy.delete(div)}}
 function layout(template,height,title,y){const base=structuredClone(template?.layout||{});return {...base,height,autosize:true,margin:{l:54,r:16,t:16,b:44},paper_bgcolor:'#fff',plot_bgcolor:'#fbfcfd',font:{family:'Arial, sans-serif',size:13,color:'#29495e'},showlegend:false,dragmode:false,hovermode:'closest',xaxis:{...(base.xaxis||{}),title:{text:'비행 시각 (초)',font:{size:12}},range:windowRange(),fixedrange:true,gridcolor:'#e4ebf0',zeroline:false},yaxis:{...(base.yaxis||{}),title:{text:title,font:{size:12}},fixedrange:true,gridcolor:'#e4ebf0',zeroline:false,...y},shapes:shapes(spans,state.time)}}
 function pairAndFleet(comm){
  const name=id=>displayDrone(id,state.manifest.fleet);
  const path=comm.route;
  const routeLabel=path===null?'확인 불가':!path.length?'우회 경로 없음':path.length===2?'직접 연결':'우회 연결';
  el('fmRouteStatus').textContent=name(state.node)+' ↔ '+name(state.peer)+' · '+routeLabel;
  el('fmRoutePath').textContent=path?.length?'현재 가용 경로  '+path.map(name).join(' → '):'';
  el('fmReachNetwork').textContent=networkLabel(comm.connected);
  el('fmGroups').textContent=(comm.groups||[]).map((group,i)=>(group.length===1?'고립':'그룹 '+(i+1))+' · '+group.map(name).join(', ')).join('  /  ');
  const nodes=comm.nodes||[];
  const hasMajority=nodes.some(n=>n.status==='connected'||n.status==='risk');
  const markup=nodes.map(n=>{
   const selected=n.id===state.node||n.id===state.peer;
   const group=(comm.groups||[]).findIndex(g=>g.includes(n.id));
   const groupText=group<0?'그룹 미확인':comm.groups[group].length===1?'고립':'그룹 '+(group+1);
   let headline=n.text,detail=n.detail;
   if(comm.connected===false&&n.status==='connected')headline='과반 그룹 내 연결';
   if(hasMajority&&n.status==='down'&&comm.groups[group]?.length>1){
    headline='과반 그룹과 분리';
    detail='';
   }
   const info=detail?`<small>${esc(detail)}</small>`:'';
   return `<button type="button" data-inspect="${esc(n.id)}" data-role="${esc(n.role)}" data-status="${esc(n.status)}" class="fm-node${selected?' fm-selected':''}" aria-label="${esc([n.label,headline,detail,'드론 A로 선택'].filter(Boolean).join(' · '))}"><span class="fm-node-name"><i></i>${esc(n.label)}</span><span>${n.role==='search'?'수색':'중계'} · ${esc(groupText)}</span><b>${esc(headline)}</b>${info}</button>`;
  }).join('');
  if(el('fmNodeStates').dataset.content!==markup){
   const focus=document.activeElement?.dataset.inspect;
   el('fmNodeStates').innerHTML=markup;
   el('fmNodeStates').dataset.content=markup;
   if(focus)Array.from(el('fmNodeStates').children).find(b=>b.dataset.inspect===focus)?.focus({preventScroll:true});
  }
 }
 function networkLabel(connected){return connected===true?'연결됨':connected===false?'단절됨':'확인 불가'}
 function riskPanel(comm,decision,used){
  const risk=comm.risk;
  const hasGate=finite(risk.score)&&finite(risk.threshold);
  const hasForecast=finite(risk.probability)&&finite(risk.time);
  const showForecast=comm.connected===true&&hasForecast;
  const decisionTime=finite(risk.time)?`${num(risk.time,1)}초 판단`:'';
  const score=el('fmScore'),probability=el('fmFleetProbability'),window=el('fmForecastWindow');
  probability.hidden=!showForecast;
  probability.textContent=showForecast?risk.probabilityText:'';
  probability.title=showForecast?risk.probabilityDetail:'';
  const showGrade=comm.connected===true&&hasGate;
  score.hidden=showForecast&&!showGrade;
  score.textContent='';
  score.dataset.grade=String(showForecast&&showGrade);
  score.dataset.risk=String(showForecast&&showGrade&&risk.score>=risk.threshold);
  score.dataset.outage=String(comm.connected===false);
  window.textContent='';
  if(comm.connected===false){
   score.textContent='현재 수색망 단절';
   window.textContent='새 단절 예측 대상 아님';
  }else if(comm.connected===null){
   score.textContent='수색망 상태 확인 불가';
  }else if(showForecast){
   if(showGrade)score.textContent='예방 판단 '+risk.label;
   window.textContent=`${num(risk.time+3,1)}초 이상 ${num(risk.time+7,1)}초 미만에 시작할 1초 이상 단절`;
  }else{
   score.textContent=risk.label==='미적용'?'예측 미적용':risk.probabilityText;
   if(showGrade)window.textContent='예방 판단 '+risk.label;
  }
  el('fmDecisionTime').textContent=showForecast?decisionTime:'';
  const gate=hasGate?`예방 판단 ${risk.label} · 점수 ${num(risk.score,4)} · 기준 ${num(risk.threshold,4)}`:'';
  el('fmScoreThreshold').textContent=[decisionTime,gate].filter(Boolean).join(' · ');
  el('fmRecordedProbability').textContent=hasForecast?risk.probabilityText:'';
  el('fmDecision').textContent=decision?.reason_label||'';
  const who=decision?.drone_id;
  el('fmIntervention').textContent=decision?.intervention_kind?`${decision.intervention_kind==='prevention'?'예방':'복구'} 개입${who!=null?' · '+displayDrone('uav-'+who,state.manifest.fleet):''}`:'';
  el('fmForecastNote').textContent=risk.probabilityDetail+' '+risk.detail;
  el('fmRecall').textContent=recordedForecast?.forecast_evaluation?.reason||'별도 기준 비행에서 평가합니다.';
  el('fmForecastPlot').hidden=!used;
 }
 async function draw(force=false){if(!state||host.hidden)return;if(drawing){pending=true;return}const nextStamp=[runKey,Math.floor(state.time*10),view,metric,rangeMode,state.node,state.peer].join('|');if(!force&&stamp===nextStamp)return;stamp=nextStamp;drawing=true;try{
 const drawRun=runKey;if(view==='evaluation'){
  const region=mapKey(state.manifest),descriptor=mapEvaluation(region);
  if(!evaluation||evaluationRegion!==region){
   evaluation?.dispose();evaluation=null;evaluationRegion=null;
   el('fmEvaluationView').textContent='이 지역의 기준 비행 평가를 불러오는 중입니다.';
   try{
    const data=descriptor?.chunk?await globalThis.__DASHBOARD_READ_CHUNK__(descriptor.chunk):(region==='bundang'?evaluationData:null);
    if(drawRun!==runKey||!state||view!=='evaluation')return;
    if(!data||data.region!==region)throw Error('선택한 지역과 일치하는 평가 자료가 없습니다.');
    evaluation=createForecastEvaluation(el('fmEvaluationView'),{Plot,data,figures,label:descriptor?.label||mapName(state.manifest)});evaluationRegion=region;
   }catch(error){if(drawRun===runKey&&view==='evaluation')el('fmEvaluationView').textContent='평가 자료를 열 수 없습니다: '+error.message;return}
  }
  evaluation.setRun({runId:state.runId,revision:state.revision,manifest:state.manifest});return
 }
 const frame=atGraph(state.overview.rf_frames,state.time,state.duration);
 const comm=commAt(state,frame,state.time,state.node,state.peer),snap=snapshot(comm);
 const isMargin=metric==='network_margin',d=METRICS[metric];
 const name=id=>displayDrone(id,state.manifest.fleet);
 el('fmTime').textContent=`${num(state.time,1)}초`;
 el('fmNetwork').textContent=networkLabel(comm.connected);
 el('fmNetwork').className=comm.connected===false?'fm-danger':comm.connected===true?'fm-positive':'';
 el('fmGraphTime').textContent=finite(snap.graphStart)?`${num(snap.graphStart,1)}초 구간 상태`:'';
 el('fmPairs').textContent=finite(snap.reachablePairs)?`${snap.reachablePairs} / ${snap.totalPairs}쌍`:'확인 불가';
 el('fmMargin').textContent=formatMargin(comm.margin);
 el('fmMargin').className=comm.margin<0?'fm-danger':finite(comm.margin)?'fm-positive':'';
 timeline();updatePair();
 if(view==='radio'){
  pairAndFleet(comm);
  if(!radio)radio=createFlightRadio(el('fmRadioHost'),{Plot,seek,fetchObservations});
  radio.setState({...state,route:comm.route,window:windowRange()});
  return;
 }
 const forecast=recordedForecast,decisions=forecast?.decisions||[],candidate=before(decisions,state.time,'time_s');
 const decision=candidate&&state.time<candidate.time_s+forecast.decision_dt_s?candidate:null;
 const used=forecast?.mode==='v21';
 riskPanel(comm,decision,used);
 el('fmMarginBasis').hidden=!isMargin;
 el('fmPacketReadout').hidden=isMargin;
 el('fmMarginSource').textContent=comm.marginReason;
 const routeDetails=[];
 if(comm.marginPair?.length)routeDetails.push('가장 취약한 필수 수색 쌍  '+comm.marginPair.map(name).join(' ↔ '));
 if(comm.marginPath?.length)routeDetails.push((comm.margin<0?'기준 미달 후보 경로  ':'여유를 산출한 경로  ')+comm.marginPath.map(name).join(' → '));
 el('fmMarginRoute').textContent=routeDetails.join('  /  ');
 const rows=state.overview.rf_frames,layoutKey=runKey+metric;
 let graphLayout=layout(figures.packet,260,d.unit,isMargin?{rangemode:'tozero'}:{ticksuffix:'%',rangemode:'normal'});
 let trace;
 if(isMargin){
  const series=comm.marginSeries||{times:[],values:[]};
  // Use RF interval starts. A missing record interrupts the step, including when the previous interval is valid.
  trace={type:'scatter',mode:'lines+markers',x:series.times,y:series.values,connectgaps:false,line:{color:'#138b80',width:2.2,shape:'hv'},marker:{size:3.5,color:'#138b80'},hovertemplate:'%{x:.1f}초 · %{y:+.2f} dB<extra></extra>'};
  graphLayout.shapes.push({type:'line',xref:'paper',yref:'y',x0:0,x1:1,y0:0,y1:0,line:{color:'#485f70',dash:'dash',width:1.5}});
  graphLayout.annotations=[{x:1,y:0,xref:'paper',yref:'y',xanchor:'right',yanchor:'bottom',text:'연결 기준 0 dB',showarrow:false,font:{size:11,color:'#465f70'},bgcolor:'rgba(255,255,255,.88)'}];
  if(finite(comm.margin))graphLayout.annotations.push({x:state.time,y:comm.margin,xref:'x',yref:'y',text:'●',font:{size:16,color:'#152f43'},showarrow:false});
  else graphLayout.annotations.push({x:.02,y:.97,xref:'paper',yref:'paper',xanchor:'left',yanchor:'top',text:'이 시각은 통신 여유 자료 부족',showarrow:false,bgcolor:'rgba(255,255,255,.9)',font:{color:'#6f7e89',size:12}});
  el('fmMetricNote').textContent=d.note;
 }else{
  const val=packetAt(rows,state.time)?.metrics?.[metric];
  el('fmMetricValue').textContent=finite(val)?num(val*d.scale,2)+d.unit:'첫 집계 전';
  el('fmMetricNote').textContent=d.note;
  trace={type:'scatter',mode:'lines',x:rows.map(r=>r.available_at_s),y:rows.map(r=>finite(r.metrics?.[metric])?r.metrics[metric]*d.scale:null),line:{color:'#138b80',width:2.6,shape:'hv'},connectgaps:false,hovertemplate:'%{x:.1f}초 · %{y:.2f}%<extra></extra>'};
 }
 plotKey=await updatePlot(el('fmPlot'),[trace],graphLayout,layoutKey,plotKey);
 if(!state||drawRun!==runKey||state.time!==snap.time||view!=='fleet')return;
 if(used&&el('fmRiskBasis').open){
  const points={type:'scatter',mode:'markers',x:decisions.map(r=>r.time_s),y:decisions.map(r=>r.score),marker:{size:7,color:'#7163ab'},hovertemplate:'%{x:.1f}초 · 점수 %{y:.4f}<extra></extra>'};
  const thresholdTrace={type:'scatter',mode:'lines',x:decisions.map(r=>r.time_s),y:decisions.map(r=>r.threshold),line:{shape:'hv',color:'#a36720',dash:'dash',width:1.4},connectgaps:false,hovertemplate:'운영 기준 %{y:.4f}<extra></extra>'};
  forecastKey=await updatePlot(el('fmForecastPlot'),[points,thresholdTrace],layout(figures.prevention,210,'판단 점수',{range:[0,1.04]}),runKey,forecastKey);
 }
 }finally{drawing=false;if(pending){pending=false;requestAnimationFrame(()=>draw(true))}}}
 return {setState(s){state=s;const key=s.runId+':'+s.revision;if(key!==runKey){runKey=key;view='fleet';plotKey='';forecastKey='';recordedForecast=fromRecordedDecisions(s);spans=outages(s.overview.rf_frames);radio?.dispose();radio=null;el('fmRadioHost').replaceChildren();el('fmFleetView').hidden=false;el('fmRadioView').hidden=true;el('fmFleet').setAttribute('aria-selected','true');el('fmRadioTab').setAttribute('aria-selected','false');viewVisibility()}draw()},openRadio(){setView('radio')},openEvaluation(){setView('evaluation')},evaluationSnapshot(){return evaluation?.snapshot?.()||null},snapshot,refresh(){draw(true)},reset(){state=null;runKey='';stamp='';recordedForecast=null;evaluation?.dispose();evaluation=null;evaluationRegion=null;el('fmEvaluationView').replaceChildren();radio?.dispose();radio=null;el('fmRadioHost').replaceChildren()},dispose(){resize.disconnect();radio?.dispose();evaluation?.dispose();Plot.purge(el('fmPlot'));Plot.purge(el('fmForecastPlot'))}};
}
