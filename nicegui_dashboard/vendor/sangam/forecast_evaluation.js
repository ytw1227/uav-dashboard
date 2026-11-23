import {esc,finite,num} from './render.js';

const PLOT_OPTIONS={responsive:true,displayModeBar:false,displaylogo:false,scrollZoom:false};
const percent=value=>finite(value)?`${num(value*100,2)}%`:'—';
const seconds=value=>finite(value)?`${num(value,1)}초`:'—';
const readJSON=id=>JSON.parse(document.getElementById(id)?.textContent||'{}');

// This component has its own completed reference-flight clock. It deliberately
// accepts no main-map seek callback and never consumes the active replay time.
export function createForecastEvaluation(host,{Plot,data,figures,label}={}){
 const source=data||readJSON('bundang-evaluation');
 const templates=figures||readJSON('flight-figures');
 const metadata=source.metadata||{},summary=source.summary||{};
 const regionLabel=label||metadata.region_label||metadata.label?.split(' · ')[0]||source.region||'선택 지역';
 const events=(source.events||[]).map((event,index)=>({...event,id:String(event.id??index+1),ordinal:index+1}));
 const alerts=source.alerts||[],series=source.series||{times:[],scores:[],valid:[]};
 const duration=metadata.duration_s??source.duration_s??Math.max(0,...series.times);
 let selectedId=events[0]?.id??null,rangeMode='local',filter='all',activeRun=null;
 let disposed=false,rendering=false,requested=0,drawn=0,visible=false,scrollFrame=0;
 let validationError='';
 if(!finite(duration)||duration<=0||!finite(metadata.threshold))validationError='평가 시각 또는 경보 기준이 없습니다.';
 else if(series.times.length!==series.scores.length||series.times.length!==series.valid.length)validationError='평가 시각·점수·유효 여부의 기록 수가 맞지 않습니다.';
 else if(events.length!==summary.events||events.filter(e=>e.hit).length!==summary.hits||alerts.length!==summary.alerts)validationError='사건별 목록과 공식 평가 집계가 일치하지 않습니다.';

 host.classList.add('forecast-evaluation');
 host.innerHTML=`<div class="fe-header"><div><span class="fe-eyebrow">${esc(regionLabel)} · 한 비행의 전체 평가</span><h4>단절을 얼마나 미리 알렸는지</h4></div><span class="fe-scope-badge">기준 비행 · 새 모델 예측 평가</span></div>
 <p class="fe-scope-note">현재 지도에서 재생하는 비행과 별도로 평가한 기준 비행입니다. 아래 사건을 선택해도 현재 지도의 재생 시각은 바뀌지 않습니다.</p>
 <p id="feError" class="fe-error" hidden></p>
 <div id="feContent"><div class="fe-cards"><article><span>선행 탐지율</span><strong id="feRecall">—</strong><small id="feRecallCount"></small></article><article><span>경보 정밀도</span><strong id="fePrecision">—</strong><small id="fePrecisionCount"></small></article><article><span>선행 시간 중앙값</span><strong id="feLead">—</strong><small id="feLeadCount"></small></article></div>
 <p id="feConditions" class="fe-conditions"></p>
 <div class="fe-graph-heading"><div><strong id="feSelectedTitle">평가 사건</strong><span id="feTime" class="fe-clock"></span></div><label>그래프 범위<select id="feRange"><option value="local">선택 사건 전후 15초</option><option value="full">전체 평가 비행</option></select></label></div>
 <div id="feSelectedSummary" class="fe-selected-summary" role="status"></div>
 <div id="fePlot" class="fe-plot" aria-label="${esc(regionLabel)} 기준 비행의 새 모델 예측 점수와 경보·단절"></div>
 <div class="fe-legend"><span data-key="score">평가 점수</span><span data-key="alert">경보 시작</span><span data-key="selected-alert">선택 사건의 대응 경보</span><span data-key="outage">평가 대상 단절</span></div>
 <p class="fe-hint">점은 기록된 평가 시각의 점수입니다. 점수가 없는 구간은 연결하지 않습니다. 경보 기준선은 적용 모델의 혼합 예측 점수 기준입니다. 실제 개입은 추가 제어 조건도 만족해야 합니다.</p>
 <div class="fe-table-heading"><div><h5>사건별 탐지 결과</h5><span id="feEventCount"></span></div><label>표시할 사건<select id="feFilter"><option value="all">전체 사건</option><option value="hit">선행 탐지 성공</option><option value="miss">선행 탐지하지 못함</option></select></label></div>
 <div class="fe-table-wrap"><table class="fe-table"><thead><tr><th>사건</th><th>단절 시각</th><th>대응 경보</th><th>선행 시간</th><th>탐지 여부</th></tr></thead><tbody id="feTableBody"></tbody></table></div>
 <div class="fe-table-footer"><span>사건을 선택하면 같은 평가 비행의 그래프를 확인할 수 있습니다.</span><div><button id="fePrev" type="button" aria-label="이전 평가 사건">‹ 이전</button><button id="feNext" type="button" aria-label="다음 평가 사건">다음 ›</button></div></div>
 <details class="fe-method"><summary>평가 조건과 집계 기준</summary><div id="feMethod"></div><p>${metadata.source_split==='train'?'학습에 사용된 지도입니다. ':metadata.source_split==='external_demonstration'?'별도 시연 조건에서 수행한 개별 평가입니다. ':''}이 한 조건의 결과는 보고서 전체 성능을 대신하지 않습니다.</p></details></div>`;
 const el=id=>host.querySelector('#'+id);
 const scoreAt=t=>{
  const index=series.times.findIndex(value=>Math.abs(value-t)<1e-7);
  return index>=0&&series.valid[index]&&finite(series.scores[index])?series.scores[index]:null;
 };
 const shownEvents=()=>events.filter(e=>filter==='all'||(filter==='hit'?e.hit:!e.hit));
 const selectedEvent=()=>events.find(e=>e.id===selectedId)||null;
 function ensureSelectedRowVisible(){
  if(disposed||!visible||!selectedId)return;
  const wrap=host.querySelector('.fe-table-wrap');
  const row=[...el('feTableBody').querySelectorAll('[data-fe-event]')].find(item=>item.dataset.feEvent===selectedId);
  if(!row||!wrap.clientHeight||!wrap.getBoundingClientRect().width)return;
  const head=wrap.querySelector('thead'),box=wrap.getBoundingClientRect(),item=row.getBoundingClientRect();
  const headerHeight=getComputedStyle(head).position==='absolute'?0:head.getBoundingClientRect().height;
  const top=box.top+wrap.clientTop+headerHeight,bottom=box.top+wrap.clientTop+wrap.clientHeight;
  // Only the list scrolls. Never scroll the page or the surrounding graph.
  if(item.top<top||item.height>bottom-top)wrap.scrollTop+=item.top-top;
  else if(item.bottom>bottom)wrap.scrollTop+=item.bottom-bottom;
 }
 function revealSelection(){
  if(scrollFrame)cancelAnimationFrame(scrollFrame);
  scrollFrame=requestAnimationFrame(()=>{scrollFrame=0;ensureSelectedRowVisible()});
 }
 function bounds(){const event=selectedEvent();if(rangeMode==='full'||!event)return [0,duration];return [Math.max(0,event.start_s-15),Math.min(duration,(finite(event.end_s)?event.end_s:event.start_s)+15)]}
 function snapshot(){const event=selectedEvent();return {activeRunId:activeRun?.runId??null,selectedEventId:selectedId,evaluationTime:event?.start_s??null,range:bounds(),filter,visibleEventIds:shownEvents().map(e=>e.id),summary:{...summary},referenceController:metadata.reference_controller,evaluatedModel:metadata.evaluated_model,validationError}}
 function selectEvent(id){if(!events.some(e=>e.id===String(id)))return;selectedId=String(id);update()}
 el('feTableBody').addEventListener('click',event=>{const row=event.target.closest('[data-fe-event]');if(row)selectEvent(row.dataset.feEvent)});
 el('feRange').onchange=event=>{rangeMode=event.target.value;update()};
 el('feFilter').onchange=event=>{filter=event.target.value;const rows=shownEvents();if(!rows.some(e=>e.id===selectedId))selectedId=rows[0]?.id??null;update()};
 for(const [id,step] of [['fePrev',-1],['feNext',1]])el(id).onclick=()=>{const rows=shownEvents(),index=rows.findIndex(e=>e.id===selectedId);if(rows[index+step])selectEvent(rows[index+step].id)};

 function renderText(){
  if(validationError){el('feError').hidden=false;el('feError').textContent=validationError;el('feContent').hidden=true;return}
  el('feContent').hidden=false;el('feError').hidden=true;
  el('feRecall').textContent=summary.events>0?percent(summary.recall):'—';
  el('feRecallCount').textContent=summary.events>0?`${summary.events}건 중 ${summary.hits}건 선행 탐지`:'평가할 단절 없음';
  el('fePrecision').textContent=summary.alerts>0?percent(summary.precision):'—';
  el('fePrecisionCount').textContent=summary.alerts>0?`경보 ${summary.alerts}건 중 ${summary.hits}건 성공`:'평가할 경보 없음';
  el('feLead').textContent=summary.hits>0?seconds(summary.median_lead_s):'—';
  el('feLeadCount').textContent=summary.hits>0?`탐지 성공 ${summary.hits}건 기준`:'탐지 성공 사건 없음';
  const spec=metadata.spec||{};
  const seed=metadata.seed??spec.seed;
  el('feConditions').textContent=`평가 비행 ${num(duration,1)}초${seed!==undefined?' · seed '+seed:''} · 경보 기준 ${num(metadata.threshold,4)} · 완료된 비행 전체의 결과`;
  const selected=selectedEvent(),rows=shownEvents(),position=rows.findIndex(e=>e.id===selectedId);
  el('feSelectedTitle').textContent=selected?`사건 ${selected.ordinal} · 단절과 경보`:'선택된 평가 사건 없음';
  el('feTime').textContent=selected?`평가 비행 시각 ${seconds(selected.start_s)}`:'평가 비행 전체';
  el('feSelectedSummary').classList.toggle('is-miss',!!selected&&!selected.hit);
  el('feSelectedSummary').innerHTML=selected?(selected.hit?`<span>경보 <strong>${seconds(selected.warning_time_s)}</strong></span><b aria-hidden="true">→</b><span>단절 <strong>${seconds(selected.start_s)}</strong></span><span class="fe-result hit">${seconds(selected.lead_s)} 전에 탐지</span>`:`<span>단절 <strong>${seconds(selected.start_s)}</strong></span><span class="fe-result miss">선행 탐지하지 못함</span><small>정해진 시간 범위와 일대일 대응 기준을 만족하는 경보가 없습니다.</small>`):'표시할 사건이 없습니다.';
  el('feEventCount').textContent=`${events.length}건 중 ${rows.length}건 표시`;
  el('feTableBody').innerHTML=rows.length?rows.map(event=>`<tr data-fe-event="${esc(event.id)}" class="${event.id===selectedId?'is-selected':''}"><td data-label="사건"><button type="button" data-fe-select="${esc(event.id)}" aria-pressed="${event.id===selectedId}" aria-label="사건 ${event.ordinal}, ${seconds(event.start_s)} 단절 선택">사건 ${event.ordinal}</button></td><td data-label="단절 시각"><strong>${seconds(event.start_s)}</strong><small>${finite(event.end_s)?seconds(event.end_s)+'까지':'종료까지 회복 미관측'}</small></td><td data-label="대응 경보">${event.hit?seconds(event.warning_time_s):'없음'}</td><td data-label="선행 시간">${event.hit?seconds(event.lead_s):'—'}</td><td data-label="탐지 여부"><span class="fe-result ${event.hit?'hit':'miss'}">${event.hit?'성공':'미탐지'}</span></td></tr>`).join(''):'<tr class="fe-empty-row"><td colspan="5">이 조건에 해당하는 사건이 없습니다.</td></tr>';
  el('fePrev').disabled=position<=0;el('feNext').disabled=position<0||position>=rows.length-1;
  const win=metadata.window||[3,7];
  el('feMethod').innerHTML=`<ul><li>1초 이상 지속된 수색망 단절을 평가하며, 비행 시작부터 끊겨 있던 구간은 제외합니다.</li><li>경보가 시작된 뒤 ${num(win[0],1)}초 이상 ${num(win[1],1)}초 미만에 시작한 단절과 일대일로 대응합니다.</li><li>선행 탐지율은 성공 건수 ÷ 단절 건수, 경보 정밀도는 성공 건수 ÷ 경보 건수입니다.</li><li>선행 시간 중앙값은 탐지에 성공한 사건만 사용합니다.</li></ul><p>선행 탐지하지 못한 단절 ${num(summary.events-summary.hits,0)}건 · 성공으로 인정되지 않은 경보 ${num(summary.false_or_late,0)}건. 미인정 경보에는 늦거나 시간 기준을 벗어난 경보 등이 포함되므로, 모두 순수 오경보라는 뜻은 아닙니다.</p><p>제공된 모델을 재학습하지 않고 적용 모델의 공식 평가 함수로 계산했습니다. 이 기준 비행에서는 평가 점수를 이동 명령에 반영하지 않았습니다.</p>`;
 }

 function figure(){
  const base=structuredClone(templates.prevention||{data:[],layout:{}}),selected=selectedEvent();
  const matched=new Set(events.filter(e=>e.hit).map(e=>e.warning_time_s));
  const threshold=metadata.threshold;
  const traces=[{...(base.data?.[0]||{}),type:'scatter',mode:'markers',name:'평가 점수',x:series.times,y:series.scores.map((score,index)=>series.valid[index]&&finite(score)?score:null),marker:{size:5,color:'#157c83',opacity:.72},connectgaps:false,hovertemplate:'평가 %{x:.1f}초<br>점수 %{y:.4f}<extra></extra>'},
   {type:'scatter',mode:'markers',name:'경보 시작',x:alerts.map(a=>a.time_s),y:alerts.map(a=>scoreAt(a.time_s)),marker:{size:10,color:'#b87322',symbol:'triangle-up',line:{color:'#fff',width:1}},customdata:alerts.map(a=>matched.has(a.time_s)?'선행 탐지 성공':'성공으로 인정되지 않은 경보'),hovertemplate:'경보 %{x:.1f}초<br>점수 %{y:.4f}<br>%{customdata}<extra></extra>'}];
  if(selected?.hit)traces.push({type:'scatter',mode:'markers',name:'대응 경보',x:[selected.warning_time_s],y:[scoreAt(selected.warning_time_s)],marker:{size:15,color:'#2765a1',symbol:'diamond',line:{color:'#fff',width:2}},hovertemplate:`이 사건의 경보 %{x:.1f}초<br>${num(selected.lead_s,1)}초 뒤 단절<extra></extra>`});
  const shapes=events.map(event=>({type:'rect',xref:'x',yref:'paper',x0:event.start_s,x1:finite(event.end_s)?event.end_s:duration,y0:0,y1:1,layer:'below',fillcolor:event.id===selectedId?'rgba(213,159,89,.29)':'rgba(213,159,89,.12)',line:{color:event.id===selectedId?'#c49a62':'rgba(0,0,0,0)',width:event.id===selectedId?1:0}}));
  shapes.push({type:'line',xref:'paper',yref:'y',x0:0,x1:1,y0:threshold,y1:threshold,line:{color:'#b87322',width:1.4,dash:'dash'}});
  if(selected)shapes.push({type:'line',xref:'x',yref:'paper',x0:selected.start_s,x1:selected.start_s,y0:0,y1:1,line:{color:'#294a61',width:2,dash:'dot'}});
  if(selected?.hit)shapes.push({type:'line',xref:'x',yref:'paper',x0:selected.warning_time_s,x1:selected.warning_time_s,y0:0,y1:1,line:{color:'#2765a1',width:1.5,dash:'dot'}});
  const layout={...(base.layout||{}),autosize:true,height:280,margin:{l:49,r:15,t:16,b:48},font:{family:'Malgun Gothic, sans-serif',size:12,color:'#2a4a60'},paper_bgcolor:'#fff',plot_bgcolor:'#fafcfd',showlegend:false,dragmode:false,hovermode:'closest',xaxis:{...(base.layout?.xaxis||{}),range:bounds(),title:{text:'평가 비행 시각 (초)',font:{size:12}},fixedrange:true,automargin:true,gridcolor:'#e4ebef',zeroline:false},yaxis:{...(base.layout?.yaxis||{}),range:[-.035,1.035],title:{text:'예측 평가 점수',font:{size:12}},fixedrange:true,automargin:true,gridcolor:'#e4ebef',zeroline:false},shapes};
  return {data:traces,layout};
 }
 async function plotLoop(){
  if(rendering||disposed||validationError||!visible)return;
  if(host.getBoundingClientRect().width<=0)return;
  rendering=true;
  try{while(!disposed&&visible&&drawn!==requested){const version=requested,fig=figure();await Plot.react(el('fePlot'),fig.data,fig.layout,PLOT_OPTIONS);drawn=version;}}
  catch(error){if(!disposed){el('feError').hidden=false;el('feError').textContent='평가 그래프 표시 오류: '+error.message;}}
  finally{rendering=false;}
 }
 function update(){if(disposed)return;renderText();revealSelection();requested++;void plotLoop()}
 const observer=new ResizeObserver(()=>{if(disposed||!visible||host.getBoundingClientRect().width<=0)return;revealSelection();if(drawn!==requested){void plotLoop()}else if(el('fePlot')._fullLayout){Plot.Plots.resize(el('fePlot'))}});observer.observe(host);
 renderText();
 return {
  setRun(run){activeRun=run;const region=run?.manifest?.region??run?.manifest?.metadata?.region;visible=region===(source.region||metadata.spec?.region);if(visible){if(!requested)update();else void plotLoop()}},
  refresh(){visible=true;revealSelection();if(!requested)update();else {void plotLoop();if(el('fePlot')._fullLayout)Plot.Plots.resize(el('fePlot'))}},
  snapshot,
  selectEvent,
  dispose(){disposed=true;visible=false;if(scrollFrame)cancelAnimationFrame(scrollFrame);observer.disconnect();Plot.purge(el('fePlot'))}
 };
}
