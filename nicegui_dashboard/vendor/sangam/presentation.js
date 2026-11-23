import {esc,num,intervalAt,time} from './render.js';
import {droneMapLabel} from './map3d.js';
import {flightRadioReading,flightRadioSamples} from './flight_radio.js';
import {incidentFocus} from './incident_focus.js';
import {recoveryRoutes} from './incident_radio.js';
import {commAt,formatMargin,detourText} from './comm_state.js';

const finite=Number.isFinite;
const modes=new Set(['around','always','hidden']);
const storageKey='uav-presentation-evidence-mode-v2';
const pairKey=pair=>(pair||[]).slice().sort().join('|');
const samePair=(link,pair)=>pairKey([link.source,link.target])===pairKey(pair);
const stamp=t=>finite(t)?num(t,1)+'초':'미기록';
const last=(rows,t,key='time_s')=>(rows||[]).findLast(row=>finite(row[key])&&row[key]<=t);
export function presentationEnabled(region){return !!globalThis.__UAV_VISUAL__?.maps?.[region]?.map_chunk;}

// Stay with an overlapping window until the next recorded outage begins.
// Preview windows describe saved events; they are never prediction alarms.
export function presentationEvent(events,t,previousId){
 const candidates=events.filter(e=>Array.isArray(e.window)&&t>=e.window[0]&&t<=e.window[1]);
 const active=candidates.filter(e=>t>=e.start_s&&t<e.end_s).at(-1);
 if(active)return active;
 const prior=candidates.find(e=>e.id===previousId);
 if(prior)return prior;
 return candidates.filter(e=>e.start_s<=t).at(-1)||candidates[0]||null;
}

export function evidencePair(event,t,frames,fleet){
 if(!event)return null;
 if(finite(event.recovery_s)&&t>=event.recovery_s){
  const routes=recoveryRoutes(event,frames,fleet);
  const frame=intervalAt(frames,t);
  for(const path of routes.paths||[])for(let i=1;i<path.length;i++){
   const pair=[path[i-1],path[i]];
   if(frame?.links?.some(link=>samePair(link,pair)&&link.available))return pair;
  }
 }
 return event.crossing_lost_links?.[0]||event.lost_links?.[0]||
  (event.default_pair?[event.default_pair.observer,event.default_pair.peer]:null);
}

export function evidenceStage(event,t,analysis,run,routes){
 if(!event)return {kind:'ordinary',label:'현재 통신 상태',detail:'선택한 링크의 저장 관측'};
 if(t<event.start_s)return {kind:'before',label:'사건 전 구간 · 기록 기준',detail:'저장 사건을 기준으로 한 사후 재생 안내입니다. 실제 예측 경보가 아닙니다.'};
 const context={event,analysis,run,routes,nominal:run.mode==='nominal'};
 return incidentFocus(context,t);
}

function item(label,value,note=''){
 return '<div class="pe-metric"><span>'+esc(label)+'</span><strong>'+esc(value)+'</strong>'+(note?'<small>'+esc(note)+'</small>':'')+'</div>';
}
function receivedNote(reading){
 if(!reading.valid)return reading.reason+(finite(reading.available)?' · 기록 '+stamp(reading.available):'');
 return (finite(reading.measured)?'측정 '+stamp(reading.measured)+' · '+num(reading.age,2)+'초 전':'측정 시차 미기록')+' · 기록 '+stamp(reading.available);
}
function sampleDots(sources,t){
 const groups=sources.map(rows=>flightRadioSamples(rows,'beacon_received_sinr_db').filter(p=>p.available<=t&&p.measured>=t-15&&p.measured<=t));
 const values=groups.flat().map(p=>p.value);if(!values.length)return '';
 const low=Math.min(...values)-1,high=Math.max(...values)+1,w=226,h=44,left=Math.max(0,t-15),width=Math.max(1,t-left);
 return '<svg class="pe-spark" viewBox="0 0 246 64" role="img" aria-label="최근 15초의 실제 수신 관측 점. 관측 사이를 연결하지 않음">'+
  '<path d="M10 4V48H236" fill="none" stroke="#69869a" stroke-width=".6"/>'+groups.map((points,i)=>points.map(p=>
   '<circle cx="'+(10+(p.measured-left)/width*w).toFixed(2)+'" cy="'+(4+(high-p.value)/(high-low)*h).toFixed(2)+'" r="2" fill="'+(i?'#ffbd70':'#65ddef')+'"/>').join('')).join('')+
  '<text x="10" y="61" fill="#afc4cf" font-size="9">'+esc(stamp(Math.max(0,t-15)))+'</text><text x="236" y="61" text-anchor="end" fill="#afc4cf" font-size="9">'+esc(stamp(t))+'</text></svg>';
}

// Draw only saved RF intervals, including gaps. The outage shading always
// follows the stored connectivity flag, independently of the margin estimate.
function marginSpark(state, frames, time) {
 const series=state.marginSeries;
 if(!series?.times?.length || !frames.length)return '';
 const from=Math.max(0,time-15), to=Math.max(15,time);
 let lo=0,hi=series.times.length;
 while(lo<hi){const mid=(lo+hi)>>>1;if(series.times[mid]<from)lo=mid+1;else hi=mid;}
 const points=[];
 for(let i=lo;i<series.times.length && series.times[i]<=time;i++){
  const t=series.times[i];
  const frame=intervalAt(frames,t);
  if(frame && frame.interval_start_s===t)points.push({t,end:Math.min(time,frame.interval_end_s),v:series.values[i],down:[0,false].includes(frame.metrics?.search_connected)});
 }
 if(!points.some(p=>finite(p.v)))return '';
 const amplitude=Math.max(1,...points.filter(p=>finite(p.v)).map(p=>Math.abs(p.v)));
 const x=t=>(3+77*(t-from)/(to-from)).toFixed(2), y=v=>(14-11*v/amplitude).toFixed(2);
 const shade=points.filter(p=>p.down).map(p=>'<rect x="'+x(p.t)+'" y="1" width="'+Math.max(.6,x(p.end)-x(p.t))+'" height="26" fill="#943349" opacity=".55"/>').join('');
 const segments=points.filter(p=>finite(p.v)).map(p=>'<path d="M'+x(p.t)+' '+y(p.v)+'H'+x(p.end)+'" stroke="#82edf1" stroke-width="1.5"/>').join('');
 const cursor='<path d="M'+x(time)+' 0v28" stroke="#fff" stroke-dasharray="2 2"/>';
 const mark=finite(state.margin)?'<circle cx="'+x(time)+'" cy="'+y(state.margin)+'" r="2.3" fill="#fff"/>':'';
 return '<svg viewBox="0 0 84 30" role="img" aria-label="최근 15초 통신 여유와 현재 재생 위치"><title>가운데 선은 연결 기준 0 dB, 점은 현재 시각입니다.</title>'+shade+'<path d="M2 14h80" stroke="#92a8b7" stroke-dasharray="2 2"/>'+segments+cursor+mark+'</svg>';
}

function describeDisconnection(state) {
 if(!state.groups.length)return state.connected===false?'수색기 그룹 확인 불가':'';
 const separated=state.nodes.filter(node=>node.status==='down');
 if(!separated.length)return '';
 const groups=new Map();
 for(const node of separated){
  if(!groups.has(node.group))groups.set(node.group,[]);
  groups.get(node.group).push(node);
 }
 const reasons=[];
 if(separated.length===state.nodes.length)reasons.push('수색기 과반 그룹 없음');
 for(const members of groups.values()){
  members.sort((a,b)=>Number(b.role==='search')-Number(a.role==='search'));
  const names=members.map(node=>node.label).join('·');
  reasons.push(names+(members.length===1?' 고립':' 그룹 분리'));
 }
 return reasons.join(' / ');
}

function drawForecast(summary,state){
 const risk=state.risk, hasTime=finite(risk.time), hasProbability=finite(risk.probability);
 const windowText=hasProbability?(risk.time+3)+'초 이상 '+(risk.time+7)+'초 미만':'';
 const primary=summary.querySelector('.pe-risk-probability'),windowNote=summary.querySelector('.pe-forecast-window');
 const knownConnection=typeof state.connected==='boolean';
 if(!knownConnection)primary.textContent='수색망 상태 확인 불가';
 else if(!state.connected)primary.textContent='새 단절 예측 대상 아님';
 else if(risk.label==='미적용')primary.textContent='예측 미적용';
 else primary.textContent=risk.probabilityText;
 const grade=summary.querySelector('.pe-current-grade');
 grade.hidden=!(state.connected===true && finite(risk.score) && finite(risk.threshold));
 grade.textContent=grade.hidden?'':'예방 판단 '+risk.label;
 windowNote.textContent=state.connected===true && hasProbability?windowText:'';
 windowNote.hidden=!windowNote.textContent;
 const history=summary.querySelector('.pe-forecast-history');
 history.hidden=!hasTime;
 if(!hasTime)return;
 summary.querySelector('.pe-history-title').textContent=stamp(risk.time)+' 판단 기록';
 const score=finite(risk.score)&&finite(risk.threshold)
     ?'예방 판단 '+risk.label+' · 점수 '+num(risk.score,4)+' / 기준 '+num(risk.threshold,4)
     :'예방 판단 점수 미기록';
 summary.querySelector('.pe-risk-value').textContent=score;
 summary.querySelector('.pe-history-note').textContent=state.connected===true && hasProbability
     ?risk.probabilityDetail
     :(hasProbability?'당시 '+risk.probabilityText+' · '+windowText:'당시 '+risk.probabilityText+'. '+risk.probabilityDetail);
}

function drawNetworkSummary(p) {
 const d=p.data,t=p.t,frames=d.overview.rf_frames||[];
 const state=d.communication||commAt({runId:d.runId,id:d.runId,revision:d.revision,region:d.region,manifest:d.manifest,overview:d.overview,mode:d.manifest.mode},intervalAt(frames,t),t);
 const summary=p.el('.pe-network');
 const status=state.connected===true?'연결':state.connected===false?'단절':'상태 확인 불가';
 summary.dataset.state=state.connected===true?'connected':state.connected===false?'down':'unknown';
 summary.querySelector('.pe-network-state').textContent='현재 수색망 '+status;
 summary.querySelector('.pe-margin-value').textContent=formatMargin(state.margin);
 summary.querySelector('.pe-margin-value').title=state.marginReason||'연결 기준 0 dB';
 const warning=summary.querySelector('.pe-warning-reason');
 warning.hidden=!state.warning.active;
 warning.textContent=state.warning.active?'여유 부족 · '+state.warning.ids.map(id=>state.nodes.find(node=>node.id===id)?.label||id).join(' ↔ '):'';
 const down=summary.querySelector('.pe-down-reason');
 down.textContent=describeDisconnection(state);
 down.hidden=!down.textContent;
 drawForecast(summary,state);
 summary.querySelector('.pe-margin-spark').innerHTML=marginSpark(state,frames,t);
}

function createController(){
 let mode='always';try{const value=localStorage.getItem(storageKey);if(modes.has(value))mode=value;}catch{}
 let main=null,mainState=null,incident=null;
 const books=new Map(),radios=new Map();
 const panels=()=>[main,incident].filter(Boolean);
 const api=({runId,revision,observer,peer,duration})=>globalThis.__PORTABLE__.api('/api/runs/'+encodeURIComponent(runId)+'/timeline?'+new URLSearchParams({revision,observer,peer,from:0,to:duration,at:duration,channels:'observations'}));
 function makePanel(parent,kind){
  const root=document.createElement('aside');root.className='presentation-evidence';root.dataset.scope=kind;
  root.setAttribute('aria-label','현재 장면의 근거');root.innerHTML=`<div class="pe-controls"><label>장면 근거 <select aria-label="근거 카드 표시"><option value="around">사건 주변</option><option value="always">항상 표시</option><option value="hidden">숨김</option></select></label><button type="button" class="pe-expand" aria-expanded="false">펼치기</button></div><div class="pe-body" hidden><div class="pe-heading"><span class="pe-event"></span><time class="pe-time"></time></div><strong class="pe-stage"></strong><div class="pe-pair"></div><small class="pe-basis"></small><div class="pe-readings"></div><div class="pe-graph"></div><p class="pe-note"></p><div class="pe-actions"><button type="button" class="pe-pin">이 드론 쌍 계속 보기</button><button type="button" class="pe-detail">상세 그래프</button></div></div>`;
  const summary=document.createElement('div');summary.className='pe-network';
  summary.innerHTML=`<div class="pe-network-title"><b class="pe-network-state"></b><span>연결 기준 0 dB</span></div>
   <div class="pe-warning-reason" hidden></div><div class="pe-down-reason" hidden></div>
   <div class="pe-network-values">
    <div><span>전체 수색망 통신 여유</span><strong class="pe-margin-value"></strong><div class="pe-margin-spark"></div></div>
    <div><span>다음 단절 예측</span><strong class="pe-risk-probability"></strong><small class="pe-current-grade"></small><small class="pe-forecast-window"></small></div>
   </div>
   <details class="pe-forecast-history" hidden><summary class="pe-history-title"></summary><div><b class="pe-risk-value"></b><span class="pe-history-note"></span></div></details>`;
  root.prepend(summary);
  const body=root.querySelector('.pe-body'), actions=body.querySelector('.pe-actions');
  const detour=document.createElement('small');detour.className='pe-pair-detour';detour.hidden=true;
  body.querySelector('.pe-pair').after(detour);
  const scroll=document.createElement('div');scroll.className='pe-content';
  for(const child of [...body.children])if(child!==actions)scroll.append(child);
  body.prepend(scroll);
  parent.append(root);const p={root,kind,key:'',t:null,previousEvent:null,expanded:false,collapsed:false,pinned:false,pair:null,mapPair:null,lastPairVersion:null,data:null,token:0};
  p.el=selector=>root.querySelector(selector);p.el('select').value=mode;
  p.el('select').onchange=e=>{mode=e.target.value;try{localStorage.setItem(storageKey,mode)}catch{};for(const q of panels()){q.el('select').value=mode;q.collapsed=false;if(mode==='hidden')q.expanded=false;draw(q)}};
  p.el('.pe-expand').onclick=()=>{const visible=!p.el('.pe-body').hidden;p.collapsed=visible;p.expanded=!visible;if(mode==='hidden'&&p.expanded){mode='around';try{localStorage.setItem(storageKey,mode)}catch{};for(const q of panels())q.el('select').value=mode}draw(p)};
  p.el('.pe-pin').onclick=()=>{p.pinned=!p.pinned;if(p.pinned){p.pair=(p.pair?.length===2?p.pair:p.mapPair)?.slice();p.expanded=true;p.collapsed=false}else{p.expanded=false;p.pair=null}draw(p)};
  p.el('.pe-detail').onclick=async()=>{
   if(p.kind==='main'){
    if(p.pair)mainState?.selectPair?.(p.pair[0],p.pair[1]);
    if(document.fullscreenElement)await document.exitFullscreen?.().catch(()=>{});
    if(document.getElementById('mapPanel')?.classList.contains('bm-expanded'))document.getElementById('expandMap')?.click();
    document.getElementById('fmRadioTab')?.click();
    document.getElementById('flightMetricsPanel')?.scrollIntoView({block:'start',behavior:'smooth'});
   }else document.getElementById('incidentRadioStack')?.scrollIntoView({block:'start',behavior:'smooth'});
  };
  return p;
 }
 async function bookFor(p){
  const key=p.data.runId+'|'+p.data.revision;
  if(books.has(key))return books.get(key);
  const descriptor=globalThis.__DASHBOARD_ANALYSIS__?.incidents?.[p.data.runId];
  if(!descriptor||descriptor.revision!==p.data.revision)return {events:[],analyses:{}};
  const pending=Promise.all([globalThis.__DASHBOARD_READ_CHUNK__(descriptor.events_chunk),globalThis.__DASHBOARD_READ_CHUNK__(descriptor.analysis_chunk)])
   .then(([book,analyses])=>({events:book.events||[],analyses}));
  books.set(key,pending);return pending;
 }
 async function observations(p,pair){
  const key=p.key+'|'+pairKey(pair);
  if(radios.has(key))return radios.get(key);
  const {runId,revision,duration}=p.data;
  const pending=Promise.all(pair.map((observer,i)=>api({runId,revision,observer,peer:pair[1-i],duration}))).then(results=>{
   const byReceiver=new Map();results.forEach((result,i)=>{
    const rows=Array.isArray(result)?result:[...(result.observations||[]),...(result.at?.observations||[])];
    const unique=new Map();for(const r of rows)if(r.observer_id===pair[i]&&r.peer_id===pair[1-i])unique.set(r.available_at_s??r.observed_at_s,r);
    byReceiver.set(pair[i],[...unique.values()].sort((a,b)=>(a.available_at_s??a.observed_at_s)-(b.available_at_s??b.observed_at_s)));
   });return byReceiver;
  });
  radios.set(key,pending);while(radios.size>10)radios.delete(radios.keys().next().value);return pending;
 }
 function bind(p,data){
  const key=data.runId+'|'+data.revision;
  if(p.key!==key){p.key=key;p.token++;p.pinned=false;p.pair=null;p.previousEvent=null;p.lastPairVersion=null;p.sources=null;p.sourceKey=null;p.book=null;}
  p.data=data;p.t=data.t;p.mapPair=data.mapPair;
  p.root.dataset.runId=data.runId;p.root.dataset.region=data.region;p.root.dataset.revision=data.revision;
  if(p.kind==='main'&&p.lastPairVersion!==null&&data.pairVersion!==p.lastPairVersion){p.pinned=true;p.pair=data.mapPair.slice();p.expanded=true;}
  p.lastPairVersion=data.pairVersion??0;
  p.root.hidden=!presentationEnabled(data.region);
  if(p.root.hidden)return;
  if(!p.book){const token=p.token;bookFor(p).then(book=>{if(token!==p.token||key!==p.key)return;p.book=book;draw(p)}).catch(()=>{if(token===p.token){p.book={events:[],analyses:{},error:true};draw(p)}})}
  draw(p);
 }
 function draw(p){
  if(!p.data||p.root.hidden)return;
  drawNetworkSummary(p);
  const d=p.data,t=p.t,events=p.book?.events||[],event=d.event||presentationEvent(events,t,p.previousEvent);
  if(event)p.previousEvent=event.id;
  const visible=mode!=='hidden'&&!p.collapsed&&(mode==='always'||!!event||p.expanded||p.pinned);
  p.el('.pe-body').hidden=!visible;p.el('.pe-expand').textContent=visible?'접기':'펼치기';p.el('.pe-expand').setAttribute('aria-expanded',String(visible));
  p.root.dataset.time=String(t);p.root.dataset.mode=mode;p.root.dataset.event=event?.id||'';
  if(!visible)return;
  const fleet=d.manifest.fleet||[],alias=id=>droneMapLabel(id,fleet),frames=d.overview.rf_frames||[];
  const run={runId:d.runId,revision:d.revision,manifest:d.manifest,overview:d.overview,mode:d.manifest.mode};
  const analysis=d.analysis||p.book?.analyses?.[event?.id];
  const routes=event?recoveryRoutes(event,frames,fleet):{paths:[]};
  const stage=evidenceStage(event,t,analysis,run,routes);
  const auto=evidencePair(event,t,frames,fleet),pair=p.pinned?p.pair:(auto||d.mapPair);
  p.pair=pair?.slice()||null;p.root.dataset.pair=pairKey(pair);p.root.dataset.selection=p.pinned?'manual':event?'stage':'map';
  const index=events.findIndex(e=>e.id===event?.id);
  p.el('.pe-event').textContent=event?(index>=0?'사건 '+(index+1):'인접 단절')+' · '+stamp(event.start_s):'선택 링크';
  p.el('.pe-time').textContent=stamp(t);p.el('.pe-stage').textContent=stage.label;
  const frame=intervalAt(frames,t);
  const link=pair?frame?.links?.find(link=>samePair(link,pair)):null;
  p.el('.pe-pair').textContent=(pair?pair.map(alias).join(' ↔ '):'링크 선택 없음')+' · '+(link?.available?'직접 연결':link?'직접 단절':'상태 미기록');
  const detour=p.el('.pe-pair-detour');
  detour.hidden=link?.available!==false;
  detour.textContent=detour.hidden?'':detourText(commAt(run,frame,t,...pair).route,alias);
  const different=pairKey(pair)!==pairKey(d.mapPair);
  p.el('.pe-basis').textContent=(p.pinned?'현재 드론 쌍 고정 중':'중요 드론 쌍 자동 선택 중')+(!p.pinned&&!event?' · 지도 선택 링크':'')+(different&&d.mapPair?.length===2?' · 지도 선택 '+d.mapPair.map(alias).join(' ↔ '):'');
  p.el('.pe-pin').textContent=p.pinned?'중요 드론 쌍 자동 선택':'이 드론 쌍 계속 보기';p.el('.pe-pin').setAttribute('aria-pressed',String(p.pinned));
  let note=stage.detail||'',metrics='';
  const action=event?last(event.interventions,t):null;
  const completed=event?last(event.interventions,t,'outcome_available_s'):null;
  if(stage.kind==='decision'&&action){
   if(finite(action.baseline_bad_s)&&finite(action.alternative_bad_s))metrics+=item('예상 통신 불량 시간',num(action.baseline_bad_s,2)+' → '+num(action.alternative_bad_s,2)+'초','당시 예측 · 원래 동작 → 대체 동작');
   if(action.expert==='cut_recovery'&&(finite(action.base_reconnect_in_s)||finite(action.projected_reconnect_in_s)))metrics+=item('예상 재연결 대기',stamp(action.base_reconnect_in_s)+' → '+stamp(action.projected_reconnect_in_s),'당시 판단 기록 · 실제 복구 시간과 구분');
   if(!metrics&&finite(action.early_score))metrics+=item('개입 당시 위험 판단 점수',num(action.early_score,4),'적용 기준 '+num(action.threshold,4));
   if(finite(action.base_reconnect_deficit_db_s)&&finite(action.projected_reconnect_deficit_db_s))metrics+=item('예상 통신 여유 부족 누적량',num(action.base_reconnect_deficit_db_s,2)+' → '+num(action.projected_reconnect_deficit_db_s,2)+' dB·s','위치 불확실성 반영 · 작을수록 좋음');
   note=alias(action.relay)+' · '+(action.reason==='recovery'?'복구':'예방')+' 보정 선택 / '+stamp(action.time_s)+' 판단 기록. '+(typeof action.actual_pre_action_connected==='boolean'?'판단 직전 수색망 '+(action.actual_pre_action_connected?'연결':'단절')+'. ':'')+'보정 사유와 실제 단절 상태는 다를 수 있습니다.';
  }else if(stage.kind==='movement'&&completed){
   metrics+=item(alias(completed.relay)+' 실제 이동',num(completed.moved_m,2)+' m',stamp(completed.time_s)+' → '+stamp(completed.outcome_available_s)+' 위치 기록');
   note='기록으로 확인한 실제 변위입니다. 보정만으로 재연결됐다는 인과 판정은 아닙니다.';
  }else if(stage.kind==='recovery')note=(stage.value||'연결 경로 회복')+' · 현재 가용 링크 기준';
  else if(stage.kind==='loss'&&finite(analysis?.margin_before_db)&&finite(analysis?.margin_at_onset_db)){
   metrics+=item('사건 분석 여유(근사)',num(analysis.margin_before_db,2)+' → '+num(analysis.margin_at_onset_db,2)+' dB','사건 유형을 판정한 사후 분석 · '+stamp(event.start_s));
   note='아래 수신 관측과 별도인 사후 분석값입니다. 당시 정책 입력으로 표시하지 않습니다.';
  }
  const sourceKey=p.key+'|'+pairKey(pair);
  if(pair?.length===2&&p.sourceKey!==sourceKey){
   p.sourceKey=sourceKey;p.sources=null;p.radioError=false;const token=p.token;
   observations(p,pair).then(rows=>{if(p.token!==token||p.sourceKey!==sourceKey)return;p.sources=rows;draw(p)}).catch(()=>{if(p.token===token&&p.sourceKey===sourceKey){p.radioError=true;draw(p)}});
  }
  const sources=pair?.map(receiver=>p.sources?.get(receiver)||[])||[[],[]];
  const readings=sources.map(rows=>flightRadioReading(rows,'beacon_received_sinr_db',t));
  const slots=Math.max(0,3-(metrics.match(/class="pe-metric"/g)||[]).length);
  for(let i=0;i<Math.min(slots,2)&&pair?.length===2;i++){
   const reading=readings[i];metrics+=item(alias(pair[1-i])+' → '+alias(pair[i])+' 비콘 SINR',!p.sources?'—':reading.valid?num(reading.value,1)+' dB':'—',!p.sources?(p.radioError?'관측을 불러오지 못함':'관측 불러오는 중'):receivedNote(reading)).replace('class="pe-metric"','class="pe-metric pe-direction-'+i+'"');
  }
  p.el('.pe-readings').innerHTML=metrics;
  p.el('.pe-graph').innerHTML=stage.kind==='decision'||stage.kind==='movement'?'':sampleDots(sources,t);
  p.el('.pe-note').textContent=note;
  scrollableEvidence(p);
  p.root.dataset.stage=stage.kind;p.root.dataset.radioReady=String(!!p.sources);p.root.dataset.pinned=String(p.pinned);
 }
 function scrollableEvidence(p){
  const area=p.el('.pe-content');
  area.dataset.scroll=String(area.scrollHeight>area.clientHeight+1);
 }
 function updateMain(payload){
  mainState=payload;
  if(!presentationEnabled(payload.state?.manifest?.region)){if(main)main.root.hidden=true;return;}
  const parent=payload.renderer.wrap;
  if(!main||main.root.parentElement!==parent){main?.root.remove();main=makePanel(parent,'main');}
  const key=payload.state.runId+'|'+payload.state.revision;
  if(main.key&&main.key!==key){main.token++;main.key='';main.data=null;main.root.hidden=true;}
 }
 function mainFrame(event){
  const detail=event.detail||{},state=mainState?.state;
  if(!main||!state||detail.data?.runId!==state.runId||detail.data?.region!==state.manifest?.region||!presentationEnabled(detail.data?.region))return;
  document.getElementById('mapClock').textContent=time(detail.renderedTime);
  const connected=detail.data.frame?.metrics?.search_connected,badge=document.getElementById('networkState');
  badge.textContent=connected===0||connected===false?'수색망 단절':connected===1||connected===true?'수색망 연결':'RF 구간 종료 / 미기록';
  badge.classList.toggle('disconnected',connected===0||connected===false);
  bind(main,{region:state.manifest.region,runId:state.runId,revision:state.revision,manifest:state.manifest,overview:state.overview,
   duration:state.duration,t:detail.renderedTime,communication:detail.data.communication,pairVersion:state.pairSelectionVersion??0,mapPair:[detail.data.selectedNode,detail.data.selectedPeer]});
 }
 function updateIncident({context:c,replayContext:replay,time:t}){
  if(!presentationEnabled(c.run.region)){resetIncident();return;}
  const parent=c.map.parentElement;
  if(!incident||incident.root.parentElement!==parent){incident?.root.remove();incident=makePanel(parent,'incident');}
  const descriptor=globalThis.__DASHBOARD_ANALYSIS__?.incidents?.[c.run.id];
  const pair=c.event.default_pair?[c.event.default_pair.observer,c.event.default_pair.peer]:c.event.crossing_lost_links?.[0];
  const radioPair=replay.radio?.snapshot?.()?.pair;
  bind(incident,{region:c.run.region,runId:c.run.id,revision:descriptor?.revision,manifest:c.run.manifest,overview:c.run.overview,
   duration:c.run.manifest.simulation_end_s??c.run.manifest.duration_s??c.run.overview.rf_frames.at(-1)?.interval_end_s,
   t,event:c.event,analysis:c.analysis,mapPair:Array.isArray(radioPair)?radioPair:pair});
 }
 function resetIncident(){if(incident){incident.token++;incident.root.remove();incident=null;}}
 document.addEventListener('uav-map-frame',mainFrame);
 const apiObject={updateMain,updateIncident,resetIncident};
 globalThis.__PRESENTATION_TEST__={snapshot:()=>({mode,main:main?{...main.root.dataset,hidden:main.root.hidden,bodyHidden:main.el('.pe-body').hidden,text:main.root.innerText}:null,incident:incident?{...incident.root.dataset,hidden:incident.root.hidden,bodyHidden:incident.el('.pe-body').hidden,text:incident.root.innerText}:null}),refresh:()=>panels().forEach(draw)};
 return apiObject;
}

if(typeof document!=='undefined'&&!globalThis.__UAV_PRESENTATION__)globalThis.__UAV_PRESENTATION__=createController();
