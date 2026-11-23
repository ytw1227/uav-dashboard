import {installPlaybackShortcuts} from './playback_shortcuts.js';
import {displayDrone} from './incident_map.js';
import {esc,finite,num,time,fmt,before,positionAt,intervalAt,packetAt,completedDecisionAt,chartSVG,MapRenderer,eventIsCensored,eventEndText,eventAfterTime} from './render.js';
import {REPRESENTATIVE_MAPS,MAP_LABELS,CORE_RF,CORE_METRICS,mapName,mapKey,mapGroups,representativeRuns,scopedRuns,missionStatus,isFinalV1,coreMetrics,coreTimeSeriesKeys,timeSeriesDefinition} from './library.js';
import {renderControlTrace,renderIntervention} from './final_v1.js';
import {renderSummaryLibrary} from './summaries.js';
import {isRepresentative,pairedFlights,sameFlightConditions,demoMapRecords,filterDemoMaps,DEMO_METRICS,demoRatio,sortDemoMaps,mapRecall,mapEvaluation} from './library.js';
import {createIncidentReplay} from './event_replay.js';
import {createFlightMetrics} from './flight_metrics.js';
import {UavMapRenderer,visualMapConfig} from './main_plotly.js';
const $=id=>document.getElementById(id);
const S={runs:[],runId:null,revision:null,manifest:null,scene:null,overview:{positions:[],rf_frames:[],decisions:[]},events:[],time:0,duration:0,window:[0,1],eventId:null,eventLimit:180,node:null,peer:null,pairSelectionVersion:0,tab:'map',playing:false,speed:10,follow:false,layers:{tracks:false,links:false,actions:false,future:false},compare:new Map(),generation:null,request:0,obsRequest:0,registryRequest:0,observations:[],obsWindow:null,online:false,compareResult:null};
const incidentReplay=createIncidentReplay(()=>{S.playing=false;renderAnalysis()});
const flightMetrics=createFlightMetrics($('flightMetricsPanel'),{
 Plot:window.Plotly,
 onEvaluationOpen:()=>{S.playing=false;renderAnalysis()},
 seek:value=>{S.playing=false;setTime(value)},
 setPair:(node,peer)=>{S.pairSelectionVersion++;S.node=node;S.peer=peer;syncSelectionControls();S.obsWindow=null;S.observations=[];renderAnalysis();requestObservations()},
 fetchObservations:({runId,revision,observer,peer,from,to,at})=>api(`/api/runs/${encodeURIComponent(runId)}/timeline?${new URLSearchParams({revision,observer,peer,from,to,at,channels:'observations'})}`)
});
window.__FLIGHT_METRICS_TEST__=window.__BUNDANG_METRICS_TEST__={evaluationSnapshot:()=>flightMetrics.evaluationSnapshot(),openEvaluation:()=>flightMetrics.openEvaluation(),snapshot:()=>flightMetrics.snapshot(),seek:t=>{S.playing=false;setTime(t)},selectRun,selectPair:(node,peer)=>{S.pairSelectionVersion++;S.node=node;S.peer=peer;syncSelectionControls();renderAnalysis()},state:()=>({runId:S.runId,time:S.time,playing:S.playing}),refresh:()=>flightMetrics.refresh()};
function renderFlightMetrics(){const supported=!!mapEvaluation(mapKey(S.manifest))||!!visualMapConfig(mapKey(S.manifest))?.map_chunk;$('flightMetricsPanel').hidden=!supported;$('legacyChartPanel').hidden=supported;if(!supported)return false;flightMetrics.setState({runId:S.runId,revision:S.revision,manifest:S.manifest,overview:S.overview,events:S.events,node:S.node,peer:S.peer,time:S.time,duration:S.duration});return true}

const labels={nominal:'기본 대형 · 미적용',v21:'11월 13일 모델 · 복구 보강',v7:'V7 기본 정책',physics:'V5 물리 기준',joint:'V5 공동학습',fixed:'V5 예측 고정',persistent_search_outage:'지속 수색망 단절',intervention:'예방·복구 개입',train:'훈련',demonstration:'시연',completed:'임무 완료',not_completed:'임무 미완료',validation:'검증',test:'시험',success:'임무 완료',failure:'임무 미완료',not_assessed:'판정 미기록',search:'수색',relay:'중계',search_outage:'수색망 단절',link_outage:'지속 링크 단절',link_onset:'지속 링크 단절',relocation:'재배치 보정',first_alert:'최초 경보'};
const seriesDefinitions={search_connected:{label:'수색망 연결 상태',unit:'boolean',scope:'RF 구간 상태'},search_pair_reachability:{label:'수색 쌍 도달률',unit:'ratio',scope:'RF 구간 상태'},fleet_connected:{label:'전체 군집 연결 상태',unit:'boolean',scope:'RF 구간 상태'},pdr_so_far:{label:'누적 PDR',unit:'ratio',scope:'선택 시각까지 · 종료 정산 전'},information_shared_fraction:{label:'정보 공유율',unit:'ratio',scope:'구간 종료 시점'},queued_packets:{label:'송신 큐',unit:'count',scope:'구간 종료 시점'},aoi_s:{label:'정보 나이 (AoI)',unit:'s',scope:'구간 종료 시점'},timely_pdr_so_far:{label:'0.25초 누적 적시 전달률',unit:'ratio',scope:'선택 시각까지 · 종료 정산 전'}};
const modelLabel=m=>labels[m]||m||'모델 미기록';
function metadata(r){return {...r,...(r?.experiment_settings||{}),...(r?.metadata||{})}}
function idOf(r){return r?.run_id}
function revisionOf(r){return r?.bundle_revision||r?.revision}
const mission=missionStatus;
function runTitle(r){return mapName(r)}
function runReady(r){return !r.conflict&&(!r.artifact_kind||r.artifact_kind==='episode')&&(r.export_status||r.status?.export_status||'ready')==='ready'&&!!revisionOf(r)}
function getMetric(key){const m=S.manifest;if(!m)return null;if(finite(m.final_metrics?.[key])||typeof m.final_metrics?.[key]==='boolean')return m.final_metrics[key];const row=(m.metrics||[]).find(x=>x.key===key||x.raw_key===key||x.metric_id===key||x.metric_id==='v5.'+key);return finite(row?.value)||typeof row?.value==='boolean'?row.value:null}
function defs(){return S.manifest?.metric_definitions||{}}
function definition(key){const d=defs();return d[key]||Object.values(d).find(x=>x.id===key)||{key,label:key,unit:'unknown'}}
function dateText(v){if(!v)return '완료시각 미기록';const d=new Date(v);return Number.isNaN(+d)?'완료시각 미기록':d.toLocaleString('ko-KR',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}
async function api(url,options={}){const response=await fetch(url,{cache:'no-cache',...options,headers:{'Accept':'application/json',...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}});let data;try{data=await response.json()}catch{throw new Error(`서비스 응답을 읽을 수 없습니다 (${response.status})`)}if(!response.ok){const e=data.error;throw new Error(typeof e==='string'?e:e?.message||data.message||`요청 실패 (${response.status})`)}return data}
function runAPI(part,params={}){const q=new URLSearchParams({revision:S.revision,...params});return `/api/runs/${encodeURIComponent(S.runId)}/${part}?${q}`}
function notice(message,error=false){$('notice').classList.toggle('hidden',!message);$('notice').classList.toggle('error',error);$('notice').textContent=message||''}
let toastTimer;function toast(message){$('toast').textContent=message;$('toast').classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.add('hidden'),4200)}
function serviceState(online,status){S.online=online;const el=$('serviceState');el.className='status-pill '+(online?'online':'offline');el.innerHTML=`<i></i>${online?'로컬 서비스 연결됨':'서비스 연결 끊김'}`;if(status){$('lastRefresh').textContent=status.scanning?'완료 결과를 검색·검증하고 있습니다.':`최근 검색 ${dateText(status.last_scan_at)} · ${status.interval_s||'—'}초 간격`;if(status.errors?.length)$('lastRefresh').textContent+=` · 확인 필요 ${status.errors.length}건`;const errors=status.errors||[];$('registryErrors').classList.toggle('hidden',!errors.length);$('registryErrorList').innerHTML=errors.map(e=>`<div style="margin-top:8px"><strong>${esc(e.code||e.error_code||'등록 보류')}</strong><br>${esc(e.message||e.reason||e.error||String(e))}${e.source_key?`<br><small>${esc(e.source_key)}</small>`:''}</div>`).join('')}else if(!online)$('lastRefresh').textContent='저장된 화면은 유지됩니다. 서비스 재연결을 기다립니다.'}
function filterRuns(){if(isDemoLibrary())return currentDemoView().visible.map(map=>map[demoMode()]);const query=$('searchInput').value.trim().toLowerCase();const values={region:$('regionFilter').value,mode:$('modeFilter').value,split:$('splitFilter').value,kind:$('kindFilter').value,direction:$('directionFilter').value,code:$('codeFilter').value,seed:$('seedFilter').value.trim(),mission:$('missionFilter').value};return scopedRuns(S.runs,$('libraryScope').value).filter(r=>{const m=metadata(r),code=m.research_version||m.source_version||'';return(!query||`${runTitle(r)} ${m.mode||''} ${m.controller||''} ${m.seed||''} ${r.run_id}`.toLowerCase().includes(query))&&(!values.region||m.region===values.region)&&(!values.mode||m.mode===values.mode)&&(!values.split||m.split===values.split)&&(!values.kind||m.kind===values.kind)&&(!values.direction||m.direction===values.direction)&&(!values.code||String(code)===values.code)&&(!values.seed||String(m.seed)===values.seed)&&(!values.mission||mission(r)===values.mission)})}
function fillFilters(){
 const demo=isDemoLibrary();
 $('demoFilters').classList.toggle('hidden',!demo);
 $('archiveFilters').classList.toggle('hidden',demo);
 $('searchInput').placeholder=demo?'지역 이름 검색':'지역, 모델, seed 검색';
 if(demo){fillGeographyFilters();return}
 $('thresholdSummary').classList.add('hidden');
 $('filterEmptyNote').classList.add('hidden');
 for(const [id,key]of [['regionFilter','region'],['modeFilter','mode'],['splitFilter','split'],['kindFilter','kind'],['directionFilter','direction'],['codeFilter','research_version']]){const el=$(id),old=el.value,title=el.options[0].textContent;const vals=[...new Set(scopedRuns(S.runs,$('libraryScope').value).map(r=>metadata(r)[key]??(key==='research_version'?metadata(r).source_version:null)).filter(x=>x!==null&&x!==undefined&&x!==''))].sort();el.innerHTML=`<option value="">${esc(title)}</option>`+vals.map(v=>`<option value="${esc(v)}">${esc(MAP_LABELS[v]||labels[v]||v)}</option>`).join('');el.value=old}}
function renderRuns(){
 if(isDemoLibrary()){renderDemoMaps();return}
 const rows=filterRuns(),groups=mapGroups(rows),versions=rows.reduce((n,r)=>n+r.versions.length,0),summaries=S.runs.filter(r=>r.artifact_kind&&r.artifact_kind!=='episode').length;
 $('runCount').textContent=groups.length+'개 지도 · '+rows.length+'개 실행';
 $('runCount').title='변환본 '+versions+'개 · 별도 성능/준비 기록 '+summaries+'개. 같은 실행은 한 카드로 표시합니다.';
 const card=(base,group)=>{const modelIds=[...new Set(group.runs.filter(x=>metadata(x).mode===metadata(base).mode).map(x=>metadata(x).model_id||'unknown'))].sort();const ordinal=modelIds.length>1?' · 모델 '+(modelIds.indexOf(metadata(base).model_id||'unknown')+1):'';const r=base.run_id===S.runId?base.versions.find(x=>revisionOf(x)===S.revision)||base:base,m=metadata(r),id=idOf(r),selected=id===S.runId,ms=mission(r);
 return `<article class="run-card ${selected?'selected':''}" data-run="${esc(id)}" data-revision="${esc(revisionOf(r))}" tabindex="0" role="button" aria-label="${esc(runTitle(r)+' '+modelLabel(m.mode))} 실행 선택">
 <div class="run-card-header"><span class="tag ${m.mode==='joint'?'teal':''}">${esc(modelLabel(m.mode))}</span><span>${esc(labels[m.split]||m.split||'분할 미기록')}</span><input type="checkbox" data-compare="${esc(id)}" data-revision="${esc(revisionOf(r))}" aria-label="${esc(runTitle(r))} 비교에 포함" ${S.compare.get(id)===revisionOf(r)?'checked':''}></div>
 <h4>${esc(modelLabel(m.mode)+ordinal)} · ${m.split==='demonstration'?(isFinalV1(r)?'대표 지도 시연':'과거 지도 시연'):'저장된 실행'}</h4><div class="run-scenario">${esc(m.kind==='continuous'?'지속 간섭':m.kind==='intermittent'?'간헐 간섭':m.kind||'시나리오 미기록')}</div>
 <div class="run-card-bottom"><span>seed ${esc(m.seed??'—')}</span><span>${esc(num(m.simulation_end_s??m.duration_s,0))} s</span></div>
 <div class="run-card-bottom" style="margin-top:8px"><span>${esc(m.direction==='east_west'?'동서 배치':m.direction==='north_south'?'남북 배치':m.direction||'배치 미기록')}</span><span class="tag ${ms==='failure'?'red':'teal'}">${esc(labels[ms]||'판정 미기록')}</span></div>
 <details class="run-history"><summary>출처${base.versions.length>1?' · 변환 이력 '+base.versions.length+'개':''}</summary><div><p>연구 완료: ${esc(dateText(r.completed_at))}</p><p>모델: <code>${esc(String(m.model_id||'미기록').slice(0,16))}</code></p><label>변환본<select data-revision-select="${esc(id)}" aria-label="변환본 선택">${base.versions.map((v,i)=>`<option value="${esc(revisionOf(v))}" ${revisionOf(v)===revisionOf(r)?'selected':''}>${i===0?'기본 · ':''}${esc(v.exported_at?dateText(v.exported_at):'변환시각 미기록')} · ${esc(String(revisionOf(v)).slice(0,12))}</option>`).join('')}</select></label><p>기본 변환본은 검증을 통과한 변환 시각 기준입니다. 연구 완료 순서와 다릅니다.</p></div></details></article>`};
 const markup=groups.length?groups.map(g=>`<section class="map-group"><div class="map-group-heading"><strong>${esc(g.name)}</strong><span>${g.runs.length}개 실행</span></div>${groups.filter(x=>x.id===g.id).length>1?`<div class="map-version">지도 버전 ${esc(String(g.hash||'미기록').slice(0,8))}</div>`:''}${representativeCards(g,card)}</section>`).join(''):'<div class="empty-state">이 보기에 재생 가능한 결과가 없습니다.<br>필터를 바꾸거나 완료 결과를 기다려 주세요.</div>';
 if(markup!==S.runListMarkup){
  const openIds=new Set([...$('runList').querySelectorAll('.run-history[open]')].map(el=>el.closest('[data-run]').dataset.run));
  const focusedRevision=document.activeElement?.dataset?.revisionSelect;
  $('runList').innerHTML=markup;S.runListMarkup=markup;
  $('runList').querySelectorAll('.run-history').forEach(el=>{if(openIds.has(el.closest('[data-run]').dataset.run))el.open=true});
  if(focusedRevision){const select=[...$('runList').querySelectorAll('[data-revision-select]')].find(el=>el.dataset.revisionSelect===focusedRevision);select?.focus({preventScroll:true})}
 }
 $('compareSelectionCount').textContent=S.compare.size+'개 실행 선택';
}
function latestEligible(){return filterRuns().filter(r=>runReady(r)&&r.completed_at&&!Number.isNaN(Date.parse(r.completed_at))&&['complete','completed'].includes(r.stage_status||r.status?.stage_status)&&(r.registration_status||r.status?.registration_status||'registered')==='registered').sort((a,b)=>Date.parse(b.completed_at)-Date.parse(a.completed_at)||a.run_id.localeCompare(b.run_id))[0]}
async function refreshRuns(initial=false,manual=false){const registryRequest=++S.registryRequest;try{if(manual){$('refreshButton').disabled=true;await api('/api/registry/rescan',{method:'POST',body:'{}'})}const [result,status]=await Promise.all([api('/api/runs'),api('/api/registry/status')]);if(registryRequest!==S.registryRequest)return;serviceState(true,status);const oldKeys=new Set(S.runs.map(r=>r.run_id+':'+revisionOf(r)));const runs=result.runs||[];const newRows=initial?[]:runs.filter(r=>!oldKeys.has(r.run_id+':'+revisionOf(r)));S.runs=runs;S.generation=result.generation??status.generation;fillFilters();renderRuns();renderFlightModes();if(newRows.length && S.tab==='compare')loadComparison();if(newRows.length){$('newBanner').classList.remove('hidden');$('newBanner').innerHTML=`<span>새 결과 또는 변환본 ${newRows.length}건이 등록되었습니다. 현재 분석 선택과 시각을 유지했습니다.</span><button class="button compact" id="dismissBanner">확인</button>`;$('dismissBanner').onclick=()=>$('newBanner').classList.add('hidden')}if(!S.runId&&runs.length){const playable=filterRuns();const preferred=playable.find(r=>isRepresentative(r)&&metadata(r).mode==='v21')||playable.find(r=>isFinalV1(r))||playable.find(r=>metadata(r).mode==='joint')||playable[0];const selectable=playable.filter(r=>!isRepresentative(r));const initialModes=new Set();for(const r of selectable){if(S.compare.size>=2)break;const mode=metadata(r).mode;if(!S.compare.has(r.run_id)&&!initialModes.has(mode)){S.compare.set(r.run_id,revisionOf(r));initialModes.add(mode)}}for(const r of selectable){if(S.compare.size>=2)break;if(!S.compare.has(r.run_id))S.compare.set(r.run_id,revisionOf(r))}if(preferred)await selectRun(preferred.run_id,revisionOf(preferred))}else if(S.follow){const latest=latestEligible();if(latest&&(latest.run_id!==S.runId||revisionOf(latest)!==S.revision))await selectRun(latest.run_id,revisionOf(latest))}if(!runs.length&&!S.manifest){$('runTitle').textContent='등록된 완료 결과가 없습니다';$('runSubtitle').textContent=status.scanning?'원본 결과를 검증하고 있습니다. 등록되면 자동으로 목록에 나타납니다.':'결과 서비스가 검증한 결과만 목록에 표시합니다.';$('mapEmpty').textContent='완료된 연구 결과를 기다리고 있습니다.'}if(manual)toast('완료 결과 재검색을 요청했습니다. 선택한 분석은 유지됩니다.')}catch(error){if(registryRequest!==S.registryRequest)return;serviceState(false);if(initial){$('runTitle').textContent='결과 서비스에 연결할 수 없습니다';$('runSubtitle').textContent='OPERATIONS의 로컬 서비스 실행 명령을 확인한 뒤 새로고침하세요.';notice(error.message,true);$('runList').innerHTML='<div class="empty-state">서비스 연결 대기 중<br>연구 시뮬레이션은 실행하지 않습니다.</div>'}else if(manual)toast(error.message)}finally{if(registryRequest===S.registryRequest)$('refreshButton').disabled=false}}
function selectedCatalogRun(){
 return S.runs.find(row => row.run_id === S.runId && revisionOf(row) === S.revision);
}

function isDemoLibrary(){return $('libraryScope').value==='representative'}
function demoMode(){return $('flightFilter').value==='nominal'?'nominal':'v21'}
function currentDemoView(){
 const view=filterDemoMaps(demoMapRecords(S.runs),{
  province:$('provinceFilter').value,district:$('districtFilter').value,mode:demoMode(),
  metric:$('performanceFilter').value,search:$('searchInput').value
 });
 view.visible=sortDemoMaps(view.visible,$('sortOrder')?.value||'',demoMode());
 return view;
}

function fillGeographyFilters(){
 const maps=demoMapRecords(S.runs);
 const options=(element,items,title)=>{
  const values=[...new Set(items)].sort((a,b)=>a.localeCompare(b,'ko'));
  const old=element.value;
  const html='<option value="">'+esc(title)+'</option>'+values.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('');
  if(element.innerHTML!==html)element.innerHTML=html;
  element.value=values.includes(old)?old:'';
 };
 const province=$('provinceFilter'),district=$('districtFilter');
 options(province,maps.map(map=>map.province),'전체 시·도');
 options(district,maps.filter(map=>map.province===province.value).map(map=>map.district),province.value?'전체 시·군·구':'시·도 먼저 선택');
 district.disabled=!province.value;
}

function renderDemoMaps(){
 const view=currentDemoView(),mode=demoMode(),metric=$('performanceFilter').value;
 const modeLabel=mode==='nominal'?'기본 대형':'모델 적용';
 const metricLabel=DEMO_METRICS[metric];
 $('runCount').textContent='표시 지도 '+view.visible.length+'개';
 $('runCount').title='';
 const summary=$('thresholdSummary');
 summary.classList.toggle('hidden',!metric);
 if(metric){
  const names=maps=>maps.map(map=>esc(map.label)).join(', ')||'없음';
  const total=view.validCount;
  const missing=view.missing.length?`<p>비교 제외 · 양쪽 지표가 필요한 지도 ${view.missing.length}곳 (${names(view.missing)})</p>`:'';
  summary.innerHTML=`<strong>${metricLabel} 95% 이하</strong><p class="threshold-counts">미적용 <b>${view.hits.nominal}/${total}곳</b> → 모델 적용 <b>${view.hits.v21}/${total}곳</b></p><p id="crossUp"><b>95% 초과로 올라감</b> · ${names(view.above)}</p><p id="crossDown"><b>95% 이하로 내려감</b> · ${names(view.below)}</p>${missing}`;
 }else summary.innerHTML='';
 const note=$('filterEmptyNote');
 const outside=!!S.runId&&!view.visible.length;
 note.classList.toggle('hidden',!outside);
 note.textContent=outside?'조건에 맞는 지도가 없습니다. 아래에는 마지막으로 열었던 비행이 표시됩니다.':'';
 const cards=view.visible.map(map=>{
  const target=map[mode],selected=map.nominal?.run_id===S.runId||map.v21?.run_id===S.runId;
  const summary=mapEvaluation(map.region)?.summary,recall=mapRecall(map),order=$('sortOrder')?.value||'';
  const recallText=summary?`<span class="demo-evaluation">선행탐지율 <b>${recall===null?'—':fmt(recall,'ratio',2)}</b> <small>${summary.events>0?summary.hits+'/'+summary.events+'건':'평가 대상 단절 없음'}</small></span>`:'';
  const shownMetrics=[...new Set([metric,order.startsWith('pdr_')?'pdr':''].filter(Boolean))];
  const value=shownMetrics.map(key=>`<span class="demo-metric">${modeLabel} · ${DEMO_METRICS[key]} ${fmt(demoRatio(target,key),'ratio',2)}</span>`).join('');
  return `<section class="map-group demo-map" data-region="${esc(map.region)}"><button type="button" class="run-card pair-card ${selected?'selected':''}" data-run="${esc(target.run_id)}" data-revision="${esc(revisionOf(target))}" aria-label="${esc(map.label)} · ${modeLabel} 비행 열기"><span class="map-group-heading"><strong>${esc(map.label)}</strong></span><span>${esc(map.province)} · ${esc(map.district)}</span>${recallText}${value}</button></section>`;
 });
 const markup=cards.join('')||'<div class="empty-state">조건에 맞는 지도가 없습니다.</div>';
 if(markup!==S.runListMarkup){$('runList').innerHTML=markup;S.runListMarkup=markup}
 $('compareSelectionCount').textContent=S.compare.size+'개 실행 선택';
}

function applyDemoFilters(){
 renderRuns();
 const view=currentDemoView(),mode=demoMode(),current=selectedCatalogRun();
 const map=view.visible.find(map=>map.region===mapKey(current))||view.visible[0];
 const target=map?.[mode];
 if(!target||(target.run_id===S.runId&&revisionOf(target)===S.revision))return;
 const sameMap=current&&mapKey(current)===map.region&&S.scene;
 const anchor=sameMap?{time:finite(S.clippedPairTime)?S.clippedPairTime:S.time,node:S.node,peer:S.peer}:null;
 return selectRun(target.run_id,revisionOf(target),anchor);
}

function flightLabel(mode){
 return mode === 'nominal' ? '기본 대형 · 미적용' : '예측·재배치 적용';
}

function renderFlightModes(){
 const row = selectedCatalogRun();
 const pair = pairedFlights(S.runs, row);
 $('flightModes').classList.toggle('hidden', !pair);
 if (!pair) return;
 const active = metadata(row).mode;
 for (const [mode,id] of [['nominal','flightNominal'],['v21','flightV21']]) {
  const button = $(id);
  button.disabled = !pair[mode] || S.flightLoading;
  button.setAttribute('aria-pressed', String(active === mode));
 }
 const absent = !pair.nominal ? '기본 대형' : !pair.v21 ? '모델 적용' : '';
 if (absent) $('flightTabNote').textContent = `${absent}의 같은 조건 비행 기록이 없습니다.`;
 else if (finite(S.clippedPairTime)) {
  $('flightTabNote').textContent = `이 비행은 ${time(S.duration)}에 끝났습니다. 비교 시각 ${time(S.clippedPairTime)} 대신 종료 시각을 표시합니다.`;
 } else $('flightTabNote').textContent = '';
}

function switchFlight(mode){
 const pair = pairedFlights(S.runs, selectedCatalogRun());
 const target = pair?.[mode];
 if (!target || S.flightLoading || S.runId === target.run_id) return;
 const anchor = S.scene && {
  time: finite(S.clippedPairTime) ? S.clippedPairTime : S.time,
  node: S.node,
  peer: S.peer,
 };
 return selectRun(target.run_id, revisionOf(target), anchor);
}

function representativeCards(group, legacyCard){
 const seen = new Set();
 const cards = [];
 for (const row of group.runs) {
  if (seen.has(row.run_id)) continue;
  const pair = pairedFlights(S.runs, row);
  if (!pair?.nominal || !pair.v21) {
   cards.push(legacyCard(row,group));
   continue;
  }
  const visiblePair = group.runs.filter(member => member.run_id === pair.nominal.run_id || member.run_id === pair.v21.run_id);
  for (const member of visiblePair) seen.add(member.run_id);
  const active = visiblePair.find(member => member.run_id === S.runId);
  const target = active || visiblePair.find(member => member.run_id === pair.v21.run_id) || visiblePair[0];
  const fields = metadata(row);
  const interference = {continuous:'지속 간섭',intermittent:'간헐 간섭',clear:'간섭 없음'}[fields.kind] || fields.kind;
  const placement = {east_west:'동서 배치',north_south:'남북 배치'}[fields.direction] || fields.direction;
  cards.push(`<button type="button" class="run-card pair-card ${active?'selected':''}" data-run="${esc(target.run_id)}" data-revision="${esc(active ? S.revision : revisionOf(target))}" aria-label="${esc(group.name)} 비교 비행 선택"><strong>기본 대형 / 모델 적용</strong><span>${esc(interference)} · ${esc(placement)}</span></button>`);
 }
 return cards.join('');
}

function persistentRecoveryStats(manifest){
 const intervals = manifest.raw_metadata?.persistent_cut_intervals;
 const flightEnd = manifest.simulation_end_s;
 if (!Array.isArray(intervals) || !finite(flightEnd) || flightEnd <= 0) return null;
 let recoveredCount = 0, recoveredSeconds = 0, unresolved = 0;
 let onsets = 0, outageSeconds = 0, lastEnd = -1;
 for (const interval of intervals) {
  if (!Array.isArray(interval) || interval.length !== 2) return null;
  const [start,end] = interval;
  if (!finite(start) || !finite(end) || start < 0 || end - start < 1 || end > flightEnd || start <= lastEnd) return null;
  lastEnd = end;
  outageSeconds += end - start;
  if (start > 0) onsets++;
  if (end === flightEnd) unresolved++;
  else if (start > 0) {
   recoveredCount++;
   recoveredSeconds += end - start;
  }
 }
 const recorded = manifest.final_metrics || {};
 if ((finite(recorded.persistent_onsets) && recorded.persistent_onsets !== onsets) ||
     (finite(recorded.persistent_cut_seconds) && recorded.persistent_cut_seconds !== outageSeconds)) return null;
 return {mean_recovery_s: recoveredCount ? recoveredSeconds/recoveredCount : null,
         recovered_count: recoveredCount, unrecovered_at_end: unresolved};
}

function comparisonMetricValue(manifest, recovery, key){
 if (key === 'duration_s') return manifest.simulation_end_s ?? manifest.final_metrics?.duration_s;
 if (key === 'mission_completed') return manifest.mission_completed ?? manifest.final_metrics?.mission_completed;
 if (key === 'mean_recovery_s' || key === 'unrecovered_at_end') return recovery?.[key] ?? null;
 return manifest.final_metrics?.[key];
}

function pairedTable(baseline, applied){
 const metrics = [
  ['search_connected_mean','연결 유지율 CTR','ratio'],
  ['pdr','패킷 전달률 PDR','ratio'],
  ['persistent_onsets','수색망 단절 횟수','count'],
  ['persistent_cut_seconds','지속 단절 누적시간','s'],
  ['mean_recovery_s','평균 복구시간','s'],
  ['unrecovered_at_end','종료 시 미복구 건수','count'],
  ['information_shared_fraction','공통 공유율','ratio'],
  ['duration_s','비행 시간','s'],
  ['mission_completed','임무 결과','mission'],
 ];
 const nominalRecovery = persistentRecoveryStats(baseline), appliedRecovery = persistentRecoveryStats(applied);
 const rows = metrics.map(([key,label,unit]) => {
  const a = comparisonMetricValue(baseline,nominalRecovery,key), b = comparisonMetricValue(applied,appliedRecovery,key);
  if (unit === 'mission') {
   const value = v => v === true ? '완료' : v === false ? '미완료' : '미기록';
   return `<tr class="paired-outcome" data-metric="${key}"><th scope="row">${label}</th><td data-arm="nominal">${value(a)}</td><td data-arm="v21">${value(b)}</td><td data-arm="delta">—</td></tr>`;
  }
  // Both recovery measures use the same recorded persistent search-outage intervals.
  const derived = key === 'mean_recovery_s' || key === 'unrecovered_at_end';
  const definitionKey = derived ? 'persistent_cut_seconds' : key;
  const definitionUnit = derived ? 's' : unit;
  const baseDefinition = baseline.metric_definitions?.[definitionKey], activeDefinition = applied.metric_definitions?.[definitionKey];
  const sameDefinition = key === 'duration_s' || (baseDefinition?.definition_hash &&
   baseDefinition.definition_hash === activeDefinition?.definition_hash && baseDefinition.unit === definitionUnit && activeDefinition.unit === definitionUnit);
  const countSuffix = key === 'persistent_onsets' ? '회' : '건';
  const display = value => unit === 'count' && finite(value) ? num(value,0) + countSuffix : fmt(value,unit,2);
  let delta = '—';
  if (sameDefinition && finite(a) && finite(b)) {
   const value = (b-a) * (unit === 'ratio' ? 100 : 1);
   const scale = unit === 'ratio' ? '%p' : unit === 'count' ? countSuffix : ' s';
   const digits = unit === 'count' ? 0 : key === 'mean_recovery_s' ? 2 : unit === 'ratio' ? (Math.abs(value) < 0.01 ? 3 : 2) : 1;
   if (value !== 0 && Math.abs(value) < 10 ** -digits) {
    delta = (value < 0 ? '감소' : '증가') + ' &lt;' + num(10 ** -digits,digits) + scale;
   } else {
    delta = (value > 0 ? '+' : '') + num(value,digits) + scale;
   }
  }
  return `<tr data-metric="${key}"><th scope="row">${label}</th><td data-arm="nominal">${display(a)}</td><td data-arm="v21">${display(b)}</td><td data-arm="delta">${delta}</td></tr>`;
 });
 const sampleSize = stats => stats ? num(stats.recovered_count,0) + '건' : '기록 없음';
 return `<table class="paired-table"><thead><tr><th scope="col">지표</th><th scope="col">기본 대형</th><th scope="col">모델 적용</th><th scope="col">차이 (적용 − 기본)</th></tr></thead><tbody>${rows.join('')}</tbody></table>
 <div class="paired-metric-notes"><p>수색망 단절 횟수는 비행 중 새로 시작해 1초 이상 이어진 단절을 한 번씩 셉니다. 누적시간에는 시작 시점부터 끊겨 있던 구간도 포함합니다.</p>
 <p>평균 복구시간은 단절 시작부터 재연결까지 모두 관측된 구간의 평균입니다(기본 대형 ${sampleSize(nominalRecovery)}, 모델 적용 ${sampleSize(appliedRecovery)}). 종료 시 미복구 건수는 비행이 끝날 때까지 이어진 1초 이상 단절 구간의 수입니다.</p></div>`;
}

async function loadPairedComparison(pair,row,request){
 const m = metadata(row);
 $('pairedHeading').textContent = `${mapName(row)} · 같은 조건의 비행 비교`;
 const interference = {continuous:'지속 간섭',intermittent:'간헐 간섭',clear:'간섭 없음'}[m.kind] || m.kind;
 const placement = {east_west:'동서 배치',north_south:'남북 배치'}[m.direction] || m.direction;
 $('pairedConditions').textContent = `같은 지도 · ${interference} · ${placement} · seed ${m.seed} · 각 방식 1회`;
 if (!pair?.nominal || !pair?.v21) {
  $('pairedConditions').textContent = `${mapName(row)} · 선택한 실행 조건`;
  $('pairedTable').innerHTML = '<p class="empty-state">이 조건에서 비교할 두 비행 기록이 아직 준비되지 않았습니다.</p>';
  return;
 }
 $('pairedTable').innerHTML = '<p class="empty-state">두 비행 결과를 불러오는 중…</p>';
 const manifestFor = member => {
  const revision = revisionOf(member);
  if (S.manifest?.run_id === member.run_id && revisionOf(S.manifest) === revision) return S.manifest;
  return api(`/api/runs/${encodeURIComponent(member.run_id)}/manifest?revision=${encodeURIComponent(revision)}`);
 };
 try {
  const [baseline,applied] = await Promise.all([manifestFor(pair.nominal),manifestFor(pair.v21)]);
  if (request !== S.pairCompareRequest) return;
  if (!sameFlightConditions(baseline,applied) || baseline.run_id !== pair.nominal.run_id ||
      applied.run_id !== pair.v21.run_id || revisionOf(baseline) !== revisionOf(pair.nominal) ||
      revisionOf(applied) !== revisionOf(pair.v21)) throw new Error('같은 조건의 비행 기록을 확인할 수 없습니다.');
  $('pairedTable').innerHTML = pairedTable(baseline,applied);
 } catch(error) {
  if (request === S.pairCompareRequest) $('pairedTable').innerHTML = `<p class="empty-state">${esc(error.message)}</p>`;
 }
}

$('flightNominal').onclick=()=>switchFlight('nominal');
$('flightV21').onclick=()=>switchFlight('v21');
const renderer=new UavMapRenderer($('mapCanvas'),selection=>{if(selection.node||selection.peer)S.pairSelectionVersion++;if(selection.node)S.node=selection.node;if(selection.peer)S.peer=selection.peer;syncSelectionControls();S.observations=[];S.obsWindow=null;requestObservations();renderAnalysis();if(mapEvaluation(mapKey(S.manifest))||visualMapConfig(mapKey(S.manifest))?.map_chunk)flightMetrics.openRadio()});
async function selectRun(id,revision=null,anchor=null){
 const candidates = revision ? S.runs : scopedRuns(S.runs,'all');
 const row = candidates.find(r => r.run_id === id && (!revision || revisionOf(r) === revision));
 if (!row) return;
 incidentReplay.reset();
 flightMetrics.reset();
 $('flightMetricsPanel').hidden=true;
 $('legacyChartPanel').hidden=true;
 const request = ++S.request;
 S.obsRequest++;
 S.flightLoading = true;
 S.runId = id;
 S.revision = revision || revisionOf(row);
 S.playing = false;
 syncPlaybackControls();
 S.eventId = null;
 S.eventLimit = 180;
 S.time = 0;
 S.clippedPairTime = null;
 S.observations = [];
 S.obsWindow = null;
 S.manifest = null;
 S.scene = null;
 S.overview = {positions:[],rf_frames:[],decisions:[]};
 S.events = [];
 $('mapHeading').textContent = '비행 경로와 네트워크';
 $('eventContext').classList.add('hidden');
 $('runTitle').textContent = runTitle(row);
 $('runSubtitle').textContent = '검증된 원본의 지도와 시간 데이터를 불러옵니다.';
 $('mapEmpty').classList.remove('hidden');
 $('mapEmpty').textContent = '실제 지도와 로그를 불러오는 중…';
 for (const id of ['nodeDetail','rfDetail','predictionDetail','actionDetail','controlTraceDetail','eventList','eventSnapshots','metricChart']) {
  $(id).innerHTML = '';
 }
 $('controlTraceBlock').classList.add('hidden');
 $('eventCount').textContent = '0';
 $('metricCards').innerHTML = Array.from({length:4},()=>'<div class="skeleton"></div>').join('');
 notice('');
 renderRuns();
 renderFlightModes();
 if (S.tab === 'compare') loadComparison();
 try {
  const manifest = await api(runAPI('manifest'));
  if (request !== S.request) return;
  S.manifest = manifest;
  if(visualMapConfig(mapKey(manifest))?.map_chunk&&!S.visualDefaultsApplied){S.layers.links=true;S.visualDefaultsApplied=true;document.querySelector('[data-layer="links"]').checked=true}
  if(!visualMapConfig(mapKey(manifest))?.map_chunk)setMapExpanded(false);
  S.duration = Number(manifest.simulation_end_s ?? manifest.duration_s ?? manifest.final_metrics?.duration_s ?? metadata(row).duration_s ?? 0);
  S.time = Math.min(anchor?.time ?? 0,S.duration);
  if (anchor && anchor.time > S.duration) S.clippedPairTime = anchor.time;
  S.window = [0,S.duration || 1];
  const fleet = manifest.fleet || [];
  S.node = fleet.find(n=>n.id===anchor?.node)?.id || fleet.find(n=>n.role==='relay')?.id || fleet[0]?.id;
  S.peer = fleet.find(n=>n.id===anchor?.peer && n.id!==S.node)?.id || fleet.find(n=>n.id!==S.node && n.role==='search')?.id || fleet.find(n=>n.id!==S.node)?.id;
  $('timeSlider').max = S.duration;
  $('timeInput').max = S.duration;
  const [scene,overview,events] = await Promise.all([
   api(runAPI('scene')),
   api(runAPI('timeline',{from:0,to:S.duration,at:S.time,channels:'positions,rf_frames,decisions',request_id:request})),
   api(runAPI('events')),
  ]);
  if (request !== S.request) return;
  S.scene = scene;
  S.overview = {positions:overview.positions||[],rf_frames:overview.rf_frames||[],decisions:overview.decisions||[]};
  S.events = (events.events||events||[]).sort((a,b)=>a.start_s-b.start_s);
  const m = metadata(manifest);
  $('runTitle').textContent = mapName(manifest);
  $('runEyebrow').textContent = `${m.split ? labels[m.split]||m.split : 'SAVED RESULT'} / ${manifest.artifact_kind||'EPISODE'}`;
  const model = isRepresentative(manifest) ? flightLabel(m.mode) : modelLabel(m.mode);
  $('runSubtitle').textContent = `${model} · ${fleet.length}대 · seed ${m.seed??'미기록'} · ${m.kind||'시나리오 미기록'} / ${m.direction||'배치 미기록'} · 임무 ${num(S.duration,0)} s`;
  $('modeBadge').textContent = model;
  $('modeBadge').className = 'tag teal';
  $('revisionBadge').textContent = m.split==='demonstration' ? (isFinalV1(manifest)?'지도 시연':'과거 V5 지도 시연') : '저장 결과';
  $('revisionBadge').title = m.split==='demonstration' ? '이 지역에서 선택한 조건으로 실행한 개별 시연입니다.' : '변환 식별자는 아래 출처에서 확인할 수 있습니다.';
  $('mapUnit').textContent = `${scene.coordinate_system?.metric_crs||scene.coordinate_system?.crs||'LOCAL'} · m`;
  $('mapEmpty').classList.toggle('hidden',!!S.overview.positions.length);
  if (!S.overview.positions.length) $('mapEmpty').textContent='이 실행은 상세 위치 로그를 제공하지 않습니다.';
  if (!anchor) renderer.reset();
  populateControls();
  await incidentReplay.setRun(S,mapKey(manifest),mapName(manifest),m.mode);
  if(request!==S.request)return;
  renderProvenance();
  renderAnalysis();
  requestObservations();
  if (m.mode==='physics') toast('V5 물리 기준 실행입니다. 학습형 단절 예측은 N/A로 표시합니다.');
 } catch(error) {
  if (request !== S.request) return;
  notice(error.message,true);
  $('mapEmpty').classList.remove('hidden');
  $('mapEmpty').textContent = '이 결과의 상세 데이터를 열 수 없습니다.';
  $('metricCards').innerHTML = '<div class="empty-state">상세 로그 미제공 · 다른 실행은 계속 선택할 수 있습니다.</div>';
 } finally {
  if (request === S.request) {
   S.flightLoading = false;
   renderRuns();
   renderFlightModes();
  }
 }
}
function populateControls(){syncSelectionControls();const available=new Set(S.overview.rf_frames.flatMap(r=>Object.keys(r.metrics||{})));$('seriesSelect').innerHTML=coreTimeSeriesKeys(S.manifest,available).filter(key=>key!=='queued_packets').map(k=>{const d=seriesDefinition(k);return `<option value="${esc(k)}">${esc(d.label||k)}</option>`}).join('');if(available.has('search_connected'))$('seriesSelect').value='search_connected';const allDefs=coreMetrics(S.manifest).map(k=>defs()[k]||Object.values(defs()).find(d=>d.key===k)).filter(d=>d&&d.comparable!==false);$('compareMetric').innerHTML=allDefs.map(d=>`<option value="${esc(d.key||d.id)}">${esc(d.label||d.key||d.id)}</option>`).join('');if(allDefs.some(d=>d.key==='search_connected_mean'))$('compareMetric').value='search_connected_mean';const types=[...new Set(S.events.map(e=>e.type))];$('eventTypeFilter').innerHTML='<option value="">모든 사건</option>'+types.map(t=>`<option value="${esc(t)}">${esc(eventLabel({type:t}))}</option>`).join('');renderEvents()}
function syncSelectionControls(){const fleet=S.manifest?.fleet||[];$('nodeSelect').innerHTML=fleet.map(n=>`<option value="${esc(n.id)}">${esc(displayDrone(n.id,fleet))} · ${esc(n.id)} · ${esc(labels[n.role]||n.role||'역할 미기록')}</option>`).join('');$('nodeSelect').value=S.node||'';$('peerSelect').innerHTML=fleet.filter(n=>n.id!==S.node).map(n=>`<option value="${esc(n.id)}">↔ ${esc(displayDrone(n.id,fleet))} · ${esc(n.id)}</option>`).join('');if(S.peer===S.node||!fleet.some(n=>n.id===S.peer))S.peer=fleet.find(n=>n.id!==S.node)?.id;$('peerSelect').value=S.peer||''}
function eventLabel(e){if(e.type==='intervention')return ({prevention:'예방 개입',recovery:'복구 개입'})[e.reason||e.source_detail?.reason]||e.label||'예방·복구 개입';return e.label||labels[e.type]||({'search_network_outage':'수색망 단절','persistent_link_outage':'지속 링크 단절','relocation_interval':'재배치 보정','link_persistent_outage':'지속 링크 단절','link_outage_onset':'지속 링크 단절'})[e.type]||e.type||'사건'}
function selectedEvent(){return S.events.find(e=>e.id===S.eventId)}
function renderEvents(){const type=$('eventTypeFilter').value,rows=S.events.filter(e=>!type||e.type===type);$('eventCount').textContent=rows.length;$('eventList').innerHTML=rows.length?rows.slice(0,S.eventLimit).map(e=>`<button class="event-row ${e.id===S.eventId?'selected':''}" data-event="${esc(e.id)}"><span class="event-icon">${(e.type?.includes('relocation')||e.type==='intervention')?'↗':e.type?.includes('alert')?'!':'↯'}</span><span class="event-main"><strong>${esc(eventLabel(e))}</strong><small>${esc((e.related_uav_ids||[]).join(' · '))}<br>${e.temporal_kind==='instant'?'순간 사건':eventIsCensored(e)?esc(eventEndText(e)):`지속 ${num(e.duration_s??e.end_s-e.start_s,1)} s`}${e.derived?' · 파생':''}</small></span><time>${time(e.start_s)}</time></button>`).join('')+(rows.length>S.eventLimit?`<button class="button compact" data-event-more style="margin:12px">다음 180건 표시 · ${S.eventLimit}/${rows.length}건</button>`:''):'<div class="empty-state">이 조건의 기록된 사건이 없습니다.</div>'}
function selectEvent(id){const e=S.events.find(e=>e.id===id);if(!e)return;if(finite(S.clippedPairTime)){S.clippedPairTime=null;renderFlightModes()}S.eventId=id;S.playing=false;S.time=e.start_s;S.window=[Math.max(0,e.start_s-15),Math.min(S.duration,(e.end_s??e.start_s)+15)];const fleet=S.manifest.fleet||[],relays=fleet.filter(n=>n.role==='relay'),search=fleet.filter(n=>n.role==='search');if(e.scope==='search_network'){const components=intervalAt(S.overview.rf_frames,e.start_s)?.components||[];const disconnected=search.find(n=>{const group=components.find(c=>c.includes(n.id))||[];return group.filter(id=>search.some(p=>p.id===id)).length<search.length});S.peer=disconnected?.id||search[0]?.id;S.node=relays.find(n=>!components.find(c=>c.includes(S.peer))?.includes(n.id))?.id||relays[0]?.id}else{S.node=(e.related_uav_ids||[]).find(id=>relays.some(n=>n.id===id))||e.related_uav_ids?.[0]||S.node;S.peer=(e.related_uav_ids||[]).find(id=>id!==S.node)||search.find(n=>n.id!==S.node)?.id||S.peer}syncSelectionControls();setTab('map');S.obsWindow=null;renderEvents();renderAnalysis();requestObservations();if(incidentReplay.open(id))return;requestAnimationFrame(()=>{const card=$('eventContext');card.focus({preventScroll:true});card.scrollIntoView({block:'nearest',behavior:'smooth'})})}

function motionStep(){return S.overview.positions.length>1?S.overview.positions[1].sample_time_s-S.overview.positions[0].sample_time_s:0}
function syncPlaybackControls(){$('playButton').textContent=S.playing?'Ⅱ':'▶';$('playButton').setAttribute('aria-label',S.playing?'저장 로그 재생 정지':'저장 로그 재생');$('playButton').setAttribute('aria-pressed',String(S.playing));$('playButton').title=!!visualMapConfig(mapKey(S.manifest))?.map_chunk?'Space · 재생 / 일시정지':'';}
function togglePlayback(){if(!S.manifest||S.flightLoading)return false;S.playing=!S.playing;if(S.time>=S.duration&&S.playing)S.time=0;renderAnalysis();return true}
function setTime(value){if(!S.manifest)return;if(finite(S.clippedPairTime)){S.clippedPairTime=null;renderFlightModes()}S.time=Math.max(0,Math.min(S.duration,Number(value)||0));renderAnalysis();const c=S.obsWindow;if(!c||S.time<c.from||S.time>c.to||c.node!==S.node||c.peer!==S.peer)requestObservations()}
function currentState(){return {position:positionAt(S.overview.positions,S.time),frame:intervalAt(S.overview.rf_frames,S.time),packet:packetAt(S.overview.rf_frames,S.time),decision:before(S.overview.decisions,S.time,'decision_time_s'),completedDecision:completedDecisionAt(S.overview.decisions,S.time)}}
function renderAnalysis(){if(!S.manifest)return;const a=currentState();renderer.update({run:{runId:S.runId,revision:S.revision,manifest:S.manifest,overview:S.overview},region:mapKey(S.manifest),runId:S.runId,mode:S.manifest.mode,fleet:S.manifest.fleet,scene:S.scene,position:a.position,positions:S.overview.positions,frame:a.frame,decision:a.decision,completedDecision:a.completedDecision,time:S.time,selectedNode:S.node,selectedPeer:S.peer,event:selectedEvent(),layers:S.layers,motionStep:motionStep()});if(!visualMapConfig(mapKey(S.manifest))?.map_chunk)$('mapClock').textContent=time(S.time);$('detailTime').textContent=`${S.time.toFixed(1)} s`;$('shareButton').title=S.window[1]-S.window[0]>240?'선택 시각 전후 최대 120초 스냅샷':`선택 창 ${num(S.window[0],1)}–${num(S.window[1],1)} s 스냅샷`;$('timeSlider').value=S.time;if(document.activeElement!==$('timeInput'))$('timeInput').value=S.time.toFixed(1);syncPlaybackControls();if(!visualMapConfig(mapKey(S.manifest))?.map_chunk){const connected=a.frame?.metrics?.search_connected;$('networkState').textContent=connected===0?'수색망 단절':connected===1?'수색망 연결':'RF 구간 종료 / 미기록';$('networkState').classList.toggle('disconnected',connected===0);}$('positionNote').textContent=a.position.interpolated?`${num(a.position.from_s,1)}–${num(a.position.to_s,1)} s 사이 화면 보간`:`저장 위치 경계 ${num(a.position.sample_time_s,1)} s`;renderMetricCards(a);renderCharts();renderInspector(a);if(S.tab==='map')renderEventContext();globalThis.__UAV_PRESENTATION__?.updateMain({state:S,current:a,renderer,openIncident:id=>incidentReplay.open(id),selectPair:(node,peer)=>{const ids=new Set((S.manifest?.fleet||[]).map(n=>n.id));if(node===peer||!ids.has(node)||!ids.has(peer))return;S.pairSelectionVersion++;S.node=node;S.peer=peer;syncSelectionControls();S.obsWindow=null;S.observations=[];renderAnalysis();requestObservations()}})}
function renderMetricCards(a){
 const final=isFinalV1(S.manifest),outageKey=final?'persistent_cut_seconds':'outage_total_s';
 const thirdCard=final?['persistent_onsets','수색망 단절 횟수']:['information_shared_fraction','공통 공유율'];
 const cards=[['search_connected_mean','연결 유지율 CTR'],['pdr','패킷 전달률 PDR'],thirdCard,[outageKey,definition(outageKey).label||(final?'지속 단절 누적':'누적 단절')]];
 $('metricCards').innerHTML=cards.map(([key,label])=>{
  const d=definition(key),v=getMetric(key),outage=key===outageKey;
  const current=key==='pdr'?a.packet?.metrics?.pdr_so_far:key==='information_shared_fraction'?a.packet?.metrics?.information_shared_fraction:key==='search_connected_mean'?a.frame?.metrics?.search_connected:null;
  const currentLabel=key==='search_connected_mean'?'현재 '+(current===1?'연결':current===0?'단절':'—'):current!==null&&current!==undefined?'현재까지 '+fmt(current,d.unit||'ratio',1):outage?'최장 '+fmt(getMetric(final?'persistent_max_s':'outage_max_s'),'s',1):'';
  const detail=key==='persistent_onsets'?'비행 중 새로 시작한 1초 이상 단절':key==='persistent_cut_seconds'?'1초 이상 지속 구간 · 초기 단절 포함':key==='pdr'?'일반 PDR · 종료 정산 포함':'전체 실행 최종';
  const value=key==='persistent_onsets'&&finite(v)?num(v,0)+'회':fmt(v,d.unit||'ratio',outage?1:2);
  return `<div class="metric-card"><div class="metric-card-top"><span>${esc(label)}</span><span class="metric-mark">${outage?'◷':key.includes('pdr')?'↗':key==='information_shared_fraction'?'▧':'⌁'}</span></div><div class="metric-value">${esc(value)}</div><div class="metric-card-bottom"><span>${esc(detail)}</span><span>${esc(currentLabel)}</span></div></div>`;
 }).join('')
}
function seriesDefinition(key){return timeSeriesDefinition(key,S.manifest?.series_definitions?.[key]||{},seriesDefinitions[key]||definition(key))}
function seriesRows(key){const recordedField=S.manifest?.series_definitions?.[key]?.available_at;const fallback=['search_connected','search_pair_reachability','fleet_connected','components','largest_component_fraction','mean_degree','isolated_fraction','edge_density','graph_efficiency','reachable_search_hops'].includes(key)?'interval_start_s':'available_at_s';return S.overview.rf_frames.map(f=>({t:f[recordedField||fallback]??f.available_at_s,v:f.metrics?.[key]}))}
function renderCharts(){if(renderFlightMetrics())return;const key=$('seriesSelect').value,d=seriesDefinition(key);const events=S.events.filter(e=>isFinalV1(S.manifest)?e.type==='persistent_search_outage':(e.scope==='search_network'||e.type?.includes('search')));$('metricChart').innerHTML=chartSVG(seriesRows(key),{unit:d.unit,selected:S.time,window:S.window,events,label:d.label,min:['ratio','boolean'].includes(d.unit)?0:null,max:['ratio','boolean'].includes(d.unit)?1:null});$('chartFootnote').textContent=`${d.description||d.label} 붉은 영역: ${isFinalV1(S.manifest)?'1초 이상 지속 수색망 단절':'수색망 단절'}. 그래프를 클릭하면 해당 시각으로 이동합니다.`}
function kv(label,value,muted=false){return `<div class="kv-row"><span>${esc(label)}</span><span class="${muted?'muted':''}">${esc(value)}</span></div>`}
const radioLabels = {
 last_ack_sinr_db: '응답 신호 품질',
 beacon_received_sinr_db: '비콘 신호 품질',
 last_ack_received_power_dbm: '응답 신호 세기',
 beacon_received_power_dbm: '비콘 신호 세기',
 ack_success_ratio_in_interval: '전송 후 응답을 받은 비율'
};

function droneLabel(id){
 const match = /^uav-(\d+)$/.exec(id);
 return match && S.manifest?.fleet ? displayDrone(id,S.manifest.fleet)+' (드론 '+match[1]+')' : (id || '미선택');
}

function radioMeasurementRow(key, observation){
 const valid = observation.valid?.[key] === true && finite(observation.values?.[key]);
 const definition = S.manifest.observation_definitions?.[key];
 const unit = definition?.unit || '';
 const value = valid ? fmt(observation.values[key], unit, 1) : '측정값 없음';
 let ageText = '';
 if(valid && key !== 'ack_success_ratio_in_interval'){
  const age = observation.ages?.[key];
  // The stored age is relative to the sampling frame, not the replay cursor.
  const frameTime = observation.observed_at_s ?? observation.available_at_s;
  ageText = finite(age) && finite(frameTime)
   ? num(age + Math.max(0, S.time - frameTime), 1) + '초 전 측정'
   : '측정 시차 미기록';
 }
 const note = ageText ? '<small class="reading-age">' + esc(ageText) + '</small>' : '';
 return '<div class="kv-row" data-rf-field="' + esc(key) + '"><span>' + esc(radioLabels[key])
  + '</span><span' + (valid ? '' : ' class="muted"') + '>' + esc(value) + note + '</span></div>';
}

function renderRadioReadings(){
 const pair = S.observations.filter(row => row.observer_id === S.node && row.peer_id === S.peer);
 const observation = before(pair, S.time, 'available_at_s');
 if(!observation)return '<p class="hint">선택한 시각까지 두 드론 사이의 통신 측정 기록이 없습니다.</p>';
 const available = observation.available_at_s ?? observation.observed_at_s;
 const direction = droneLabel(S.peer) + ' → ' + droneLabel(S.node);
 return '<p class="inspector-context">' + esc(direction) + ' 수신 신호 · ' + num(available,1) + '초 공개 기록</p>'
  + CORE_RF.map(key => radioMeasurementRow(key, observation)).join('')
  + '<p class="hint">응답(ACK)은 패킷을 받았다는 신호이고, 비콘은 주기적으로 보내는 신호입니다. 신호 품질(SINR)은 값이 클수록 잡음·간섭에 비해 신호가 뚜렷합니다. 응답 비율은 해당 구간에 데이터를 보내고 답을 받은 비율입니다.</p>';
}

const finalPredictionWindows = {
 next_onset_lt5s: '5초 미만',
 next_onset_3to4s: '3초 이상 4초 미만',
 next_onset_3to7s: '3초 이상 7초 미만',
 next_onset_lt7s: '7초 미만'
};

function predictionWindowText(prediction, final){
 if(final && finalPredictionWindows[prediction.head_id])return finalPredictionWindows[prediction.head_id];
 const window = prediction.target_window ?? prediction.target_window_s;
 if(!window)return S.manifest.head_definitions?.[prediction.head_id]?.label || '대상 구간 미기록';
 const from = Array.isArray(window) ? window[0] : (window.start_s ?? window.from_s);
 const to = Array.isArray(window) ? window[1] : (window.end_s ?? window.to_s);
 const legacyLabel = prediction.head_id === 'risk_5s' ? '지속 단절 위험' : '단절 시작';
 return (final ? '' : legacyLabel + ' · ') + '비행 ' + num(from,1) + '–' + num(to,1) + '초'
  + (window.nominal_window_only ? ' (명목 구간)' : window.upper_inclusive === false ? ' (끝 미포함)' : '');
}

function renderLinkPredictions(decision, role, final){
 if(metadata(S.manifest).mode === 'physics')return '<p class="hint">이 비행은 물리 기준으로 실행되어 학습 모델의 단절 예측을 사용하지 않았습니다.</p>';
 if(role === 'search')return '<p class="hint">예측을 확인하려면 관측 기체에서 중계 드론을 선택하세요. 예측은 중계 드론과 상대 드론 사이의 통신 경로에 대해 기록됩니다.</p>';
 const predictions = (decision?.predictions || []).filter(p =>
  p.observer_id === S.node && p.peer_id === S.peer && p.generated_at_s <= S.time
  && (p.available_at_s ?? p.generated_at_s) <= S.time);
 if(!predictions.length)return '<p class="hint">선택한 시각까지 이 두 드론의 예측 기록이 없습니다.</p>';
 const origin = num(predictions[0].generated_at_s,1) + '초';
 const source = final ? (metadata(S.manifest).mode==='nominal'?' · 기본정책 예측 · 이동에 미적용':' · 11월 13일 단절 시점 예측') : '';
 const rows = predictions.map(prediction => {
  const value = prediction.supported && finite(prediction.value) ? fmt(prediction.value,'ratio',1) : '예측 불가';
  const className = prediction.head_id === 'next_onset_lt5s' ? 'risk-row risk-primary' : 'risk-row';
  return '<div class="' + className + '" data-head="' + esc(prediction.head_id) + '"><span>'
   + esc(predictionWindowText(prediction,final)) + '</span><strong>' + esc(value) + '</strong></div>';
 }).join('');
 const connection = esc(droneLabel(S.node)) + '와 ' + esc(droneLabel(S.peer)) + ' 사이';
 const subject = connection + (final
  ? '에서 다음 통신 단절(1초 이상)이 각 시간 구간에 시작될 확률입니다.'
  : '의 통신 단절 위험을 예측한 값입니다.');
 const clock = final ? '예측한 시점부터 걸리는 시간' : '예측 대상 비행 시각';
 return '<p class="inspector-context">' + subject + '</p><p class="hint">' + origin + '에 계산' + source
  + ' · ' + clock + '</p>' + rows;
}

function renderInspector(a){
 const node = a.position.uavs.find(n => n.id === S.node);
 const role = node?.role || (S.manifest.fleet || []).find(n => n.id === S.node)?.role;
 $('nodeDetail').innerHTML = kv('받는 드론', droneLabel(S.node) + ' · ' + (labels[role] || role || '역할 미기록'))
  + kv('위치 x · y', node ? num(node.x,1) + ' · ' + num(node.y,1) + ' m' : '미기록')
  + kv('고도', node ? fmt(node.z,'m',1) : '미기록')
  + kv('위치 기준', a.position.interpolated ? '화면 보간 · 두 기록 사이의 위치' : num(a.position.sample_time_s,1) + '초 기록', true);
 $('rfDetail').innerHTML = renderRadioReadings();
 const final = isFinalV1(S.manifest);
 const nominal = metadata(S.manifest).mode === 'nominal';
 $('predictionDetail').innerHTML = nominal
  ? '<p class="hint">기본 대형 비행에서는 단절 예측을 사용하지 않습니다.</p>' : renderLinkPredictions(a.decision, role, final);
 $('controlTraceBlock').classList.toggle('hidden', !final || nominal);
 $('controlTraceDetail').innerHTML = final && !nominal ? renderControlTrace(a.decision?.control_trace,a.decision?.decision_time_s) : '';
 renderActionDetail(a);
}

function renderActionDetail(a){
 const decision = a.decision;
 const action = decision?.actions?.find(row => row.uav_id === S.node);
 const detail = $('actionDetail');
 if(!action){
  detail.innerHTML = '<p class="hint">이 드론의 경로 보정 명령이 기록되지 않았습니다. 위치와 이동 경로는 지도에서 볼 수 있습니다.</p>';
  return;
 }
 const vector = xy => Array.isArray(xy) ? '[' + xy.map(n=>num(n,1)).join(', ') + '] m' : '미기록';
 const actualAt = action.actual_available_at_s ?? action.interval_end_s ?? decision.decision_time_s + motionStep();
 const ready = S.time >= actualAt;
 const showOutcome = ready || S.layers.future;
 const nominal = metadata(S.manifest).mode === 'nominal';
 detail.innerHTML = kv('결정 시각', num(decision.decision_time_s,1) + '초')
  + kv('요청한 경로 보정', vector(action.requested_correction))
  + kv('적용한 경로 보정', vector(action.applied_correction))
  + kv('이 구간의 실제 이동', showOutcome ? vector(action.actual_displacement) : num(actualAt,1) + ' s 이후 확정', !ready)
  + completedActionDetail(a.completedDecision);
 let note = nominal ? '기본 대형의 비행 계획을 따라 이동합니다.' : '보정이 0이면 원래 비행 계획을 따릅니다.';
 if(action.changed_by_protection)note += ' 요청한 보정과 실제로 적용한 보정이 달랐습니다.';
 if(!ready && S.layers.future)note += ' 이 구간의 이동 결과는 비행 후에 확인한 값입니다.';
 detail.innerHTML += '<p class="hint">' + note + '</p>';
 if(isFinalV1(S.manifest) && !nominal){
  const event = S.events.find(e => e.type === 'intervention'
   && Math.abs(e.start_s - decision.decision_time_s) < 1e-6 && (e.related_uav_ids || []).includes(S.node));
  if(event)detail.innerHTML += renderIntervention(event,S.time,S.layers.future);
 }
}
function completedActionDetail(decision){const a=decision?.actions?.find(x=>x.uav_id===S.node);if(!a||!Array.isArray(a.actual_displacement))return kv('최근 확정 실제 변위','아직 완료 구간 없음',true);return kv('최근 확정 실제 변위',`[${a.actual_displacement.map(n=>num(n,1)).join(', ')}] m`)+kv('변위의 실행 구간',`${num(a.interval_start_s??decision.decision_time_s,1)}–${num(a.actual_available_at_s??a.interval_end_s,1)} s`,true)}

let obsPendingKey=null;
async function requestObservations(){if(!S.manifest||!S.node||!S.peer)return;const from=Math.max(0,S.time-22),to=Math.min(S.duration,S.time+22),key=`${S.runId}:${S.revision}:${S.node}:${S.peer}:${Math.floor(S.time/10)}`;if(obsPendingKey===key)return;obsPendingKey=key;const runRequest=S.request,request=++S.obsRequest,node=S.node,peer=S.peer;try{const data=await api(runAPI('timeline',{from,to,at:S.time,observer:node,peer,channels:'observations',request_id:`obs-${request}`}));if(request!==S.obsRequest||runRequest!==S.request||node!==S.node||peer!==S.peer)return;const rows=[...(data.observations||[]),...(data.at?.observations||[])].filter(o=>o.observer_id===node&&o.peer_id===peer).map(o=>({...o,available_at_s:o.available_at_s??o.observed_at_s}));const unique=new Map(rows.map(o=>[o.available_at_s,o]));S.observations=[...unique.values()].sort((a,b)=>a.available_at_s-b.available_at_s);S.obsWindow={from:Math.max(0,from+5),to:Math.min(S.duration,to-5),node,peer};renderInspector(currentState())}catch(error){if(request===S.obsRequest){S.observations=[];$('rfDetail').innerHTML=`<p class="hint">관측 조회 실패: ${esc(error.message)}</p>`}}finally{if(obsPendingKey===key)obsPendingKey=null}}
function renderEventContext(){const e=selectedEvent();$('eventContext').classList.toggle('hidden',!e);if(!e)return;$('eventContextTitle').textContent=`${eventLabel(e)} · ${time(e.start_s)} 전후 연결`;const step=S.overview.rf_frames.length>1?S.overview.rf_frames[1].interval_start_s-S.overview.rf_frames[0].interval_start_s:.5;const stamps=[['사건 전',Math.max(0,e.start_s-step)],['사건 시작',e.start_s],['이후',eventAfterTime(e,S.duration,step,motionStep())]];$('eventSnapshots').innerHTML=stamps.map(([label,t])=>{const f=t===null?null:intervalAt(S.overview.rf_frames,t);const components=f?.components||[];return `<div class="event-snapshot"><div><span class="stage">${label}</span><time>${t===null?'미관측':time(t)}</time></div><div class="components">${t===null?esc(eventEndText(e)):`연결 그룹 ${components.length||'—'}개<br>`+components.map(ids=>`<span class="component-pill">${esc(ids.join(' · '))}</span>`).join('')}</div></div>`}).join('')+`<div style="grid-column:1/-1;font-size:10px;color:#718397">정의 ${esc(e.definition_id||'미기록')} · ${e.derived?'로그에서 파생':'기록 사건'} · 확인 가능 ${num(e.confirmation_time_s,1)} s · ${esc(eventEndText(e))}${eventRecoveryNote(e,step)}</div>`+(e.type==='intervention'?`<div class="event-intervention">${renderIntervention(e,S.time,S.layers.future)}</div>`:'')}
function eventRecoveryNote(e,step){if(eventIsCensored(e)||e.temporal_kind==='instant'||e.scope!=='search_network')return '';const at=intervalAt(S.overview.rf_frames,e.end_s),next=intervalAt(S.overview.rf_frames,e.end_s+step);return at?.metrics?.search_connected===1&&next?.metrics?.search_connected===0?` · ${num(e.end_s,1)} s 회복 후 ${num(e.end_s+step,1)} s 재단절 관측. 지속 회복으로 판정하지 않습니다.`:''}

function renderProvenance(){const m=S.manifest,scene=S.scene,cs=scene?.coordinate_system||{};const capabilities=m.capabilities||{};$('provenanceContent').innerHTML=`<div class="filter-grid" style="grid-template-columns:1fr 1fr;gap:18px"><div>${kv('실행 ID',m.run_id)}${kv('변환 revision',m.bundle_revision)}${kv('연구 버전',m.research_version||'미기록')}${kv('모델 ID',m.model_id||'미기록')}${kv('실제 연구 완료시각',dateText(m.completed_at))}${kv('시간 상태',m.completion_time_status||'미기록')}</div><div>${kv('공통 schema / exporter',`${m.schema_version} / ${m.exporter_version||m.adapter_version}`)}${kv('지도 SHA256',m.map_hash||'미기록')}${kv('지도 CRS',cs.metric_crs||cs.crs||'미기록')}${kv('로컬 원점',JSON.stringify(cs.origin||cs.origin_m||'미기록'))}${kv('높이 규약',cs.z_axis||cs.z||'scene 정의 참조')}${kv('지도 simulation_ready',String(scene?.metadata?.simulation_ready??scene?.simulation_ready??'미기록'))}</div></div><details style="margin-top:12px"><summary>가용 기능과 누락 사유</summary><pre style="white-space:pre-wrap;font:10px/1.8 inherit">${esc(JSON.stringify(capabilities,null,2))}</pre></details><details><summary>기록 시각 정의</summary><pre style="white-space:pre-wrap;font:10px/1.8 inherit">${esc(JSON.stringify(m.clock_definitions||m.clocks,null,2))}</pre></details><table class="definition-table"><thead><tr><th>지표</th><th>원본 값</th><th>단위 / 범위</th><th>분모 · 집계</th><th>정의 버전</th></tr></thead><tbody>${Object.entries(defs()).map(([key,d])=>`<tr><td>${esc(d.label||key)}<br><span style="color:#8c9baa">${esc(key)}</span></td><td>${esc(fmt(getMetric(key),d.unit))}</td><td>${esc(d.unit)} / ${esc(d.scope)}</td><td>${esc(d.denominator)}<br>${esc(d.aggregation)}</td><td>${esc(d.version||d.definition_hash)}</td></tr>`).join('')}</tbody></table><p class="hint">전체 실행 최종 PDR은 종료 후 큐 drain을 포함합니다. 시간 차트의 누적 PDR은 drain 전입니다. 과거 결과를 현재 연구 코드의 성능으로 재표시하지 않습니다.</p>`}
function setTab(tab){
 tab=tab==='events'?'map':tab;if(tab!=='map')setMapExpanded(false);S.tab=tab;document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
 $('analysisView').classList.toggle('hidden',tab!=='map');$('compareView').classList.toggle('hidden',tab!=='compare');$('performanceView').classList.toggle('hidden',tab!=='performance');$('eventContext').classList.toggle('hidden',tab!=='map'||!S.eventId);
 if(tab==='compare'){S.playing=false;syncPlaybackControls();loadComparison()}else if(tab==='performance'){S.playing=false;syncPlaybackControls();loadPerformance()}else{$('mapHeading').textContent='비행 경로와 네트워크';renderAnalysis();requestAnimationFrame(()=>renderer.draw())}
}
let performanceRequest=0;
async function loadPerformance(){const request=++performanceRequest;$('performanceContent').innerHTML='<div class="empty-state">현재 모델 정보를 불러옵니다…</div>';try{const data=await api('/api/summaries');if(request===performanceRequest)$('performanceContent').innerHTML=renderSummaryLibrary(data)}catch(e){if(request===performanceRequest)$('performanceContent').innerHTML='<div class="empty-state">현재 모델 정보를 열 수 없습니다.<br>'+esc(e.message)+'</div>'}}

async function loadComparison(){
 const row = selectedCatalogRun();
 const pair = pairedFlights(S.runs,row);
 const request = S.pairCompareRequest = (S.pairCompareRequest || 0) + 1;
 $('pairedComparison').classList.toggle('hidden', !pair);
 $('legacyComparison').classList.toggle('hidden', !!pair);
 if (pair) {
  S.compareRequest = null;
  return loadPairedComparison(pair,row,request);
 }
 if(S.compare.size<1){S.compareRequest=null;$('compareContent').innerHTML='<div class="empty-state">왼쪽 목록에서 비교할 실행을 선택하세요.</div>';return}const metric=$('compareMetric').value||'search_connected_mean',runs=[...S.compare].map(([run_id,revision])=>({run_id,revision})),key=JSON.stringify({runs,metric});S.compareRequest=key;$('compareContent').innerHTML='<div class="empty-state">저장된 지표와 비교 조건을 확인하고 있습니다…</div>';try{const result=await api('/api/compare',{method:'POST',body:JSON.stringify({runs,metric_ids:[metric]})});if(S.compareRequest!==key)return;S.compareResult=result;renderComparison(result,metric)}catch(error){if(S.compareRequest===key)$('compareContent').innerHTML=`<div class="empty-state">비교를 열 수 없습니다.<br>${esc(error.message)}</div>`}}
function metricFromRow(row,key){const m=row.metrics;if(Array.isArray(m)){const d=m.find(x=>x.key===key||x.metric_id===key||x.metric_id==='v5.'+key);return d?.value}return m?.[key]?.value??m?.[key]??row.final_metrics?.[key]}
function renderComparison(result,key){const d=definition(key),statUnit=d.unit==='boolean'?'ratio':d.unit,rows=result.runs||[];const counts=result.counts||{};$('compareWarnings').innerHTML=(result.warnings||[]).map(w=>`<div class="comparison-warning">${esc(typeof w==='string'?w:w.message||JSON.stringify(w))}</div>`).join('');let html=`<div class="comparison-stats"><div class="comparison-stat"><small>선택 실행</small><strong>${rows.length||S.compare.size}</strong></div><div class="comparison-stat"><small>임무 미완료</small><strong>${counts.mission_failure??counts.mission_failed??rows.filter(r=>mission(r)==='failure').length}</strong></div><div class="comparison-stat"><small>프로세스 실패·중단</small><strong>${counts.process_failure_or_interrupted??((counts.process_failed??0)+(counts.interrupted??0))}</strong></div><div class="comparison-stat"><small>데이터 오류 / 제외</small><strong>${counts.data_error??counts.data_errors??counts.invalid??0} / ${counts.excluded??((counts.summary_only??0)+(counts.duplicate??0)+(counts.parent_excluded??0))}</strong></div></div>`;const groups=result.groups||[];if(groups.length){for(const group of groups){const stats=Array.isArray(group.metrics)?group.metrics.find(m=>m.key===key||m.metric_id===key||m.metric_id==='v5.'+key)||group.metrics[0]:group.metrics?.[key]||group;const groupDefinition={...d,...(stats?.definition||{})},statUnit=groupDefinition.unit==='boolean'?'ratio':groupDefinition.unit;const ids=group.run_ids||group.runs?.map(r=>typeof r==='string'?r:r.run_id);const members=ids?rows.filter(r=>ids.includes(r.run_id)):rows.filter(r=>!r.definition_signature||r.definition_signature===group.definition_signature);const values=members.map(r=>({id:r.run_id,label:runTitle(r),value:typeof metricFromRow(r,key)==='boolean'?Number(metricFromRow(r,key)):metricFromRow(r,key)})).filter(r=>finite(r.value));html+=`<section class="compare-group"><div class="compare-group-heading"><span>${esc(groupDefinition.label||key)} <span class="tag">${groups.length>1?'조건별 분리':'동일 조건 그룹'}</span> <small style="font-weight:400;color:#718397">${esc(group.metadata?.region||'')} · ${esc(modelLabel(group.metadata?.mode))}</small></span><span class="tag muted">${esc(String(group.condition_signature||group.definition_signature||stats?.definition_hash||'').slice(0,14))}</span></div><div class="comparison-stats"><div class="comparison-stat"><small>표본 수 n</small><strong>${stats?.n??values.length}</strong></div><div class="comparison-stat"><small>실행별 균등 평균</small><strong>${fmt(stats?.mean,statUnit,2)}</strong></div><div class="comparison-stat"><small>최악값</small><strong>${fmt(stats?.worst??(groupDefinition.preferred_direction==='lower'?stats?.max:stats?.min),statUnit,2)}</strong></div><div class="comparison-stat"><small>중앙값</small><strong>${fmt(stats?.median,statUnit,2)}</strong></div></div><div class="distribution">${distributionSVG(values,{...groupDefinition,unit:statUnit})}</div>${key==='pdr'&&group.pooled_pdr?`<p class="panel-footnote">동일 조건 패킷 합산 PDR ${fmt(group.pooled_pdr.value,'ratio',3)} · 전달 ${num(group.pooled_pdr.delivered,0)} / 생성 ${num(group.pooled_pdr.generated,0)} 패킷. 위 실행별 균등 평균과 분모가 다릅니다.</p>`:''}</section>`}}else html+='<div class="empty-state">호환되는 지표 그룹이 없습니다. 정의와 제외 사유를 확인하세요.</div>';html+=`<table class="comparison-table"><thead><tr><th>실행 / 지도</th><th>모델 · seed · 분할</th><th>${esc(d.label||key)}</th><th>임무 상태</th><th>출처</th></tr></thead><tbody>${rows.map(r=>{const m=metadata(r);return `<tr><td>${esc(runTitle(r))}<br><small>${esc(r.run_id?.slice(0,18))}</small></td><td>${esc(modelLabel(m.mode))} · ${esc(m.seed??'—')}<br>${esc(labels[m.split]||m.split||'미기록')}</td><td class="value-cell">${esc(fmt(metricFromRow(r,key),r.metrics?.[key]?.definition?.unit||d.unit,3))}</td><td>${esc(labels[mission(r)]||mission(r))}</td><td><details><summary>보기</summary><small>실행 ${esc(r.run_id)}<br>변환 ${esc(r.bundle_revision||r.revision||S.compare.get(r.run_id)||'미기록')}</small></details></td></tr>`}).join('')}</tbody></table>`;if(key==='pdr'&&result.pooled_pdr){html+=`<p class="panel-footnote">패킷 수로 합산한 PDR: ${fmt(result.pooled_pdr.value,'ratio',3)} · ${esc(result.pooled_pdr.reason||result.pooled_pdr.scope||'동일 비교 조건에서만 제공')}. 위 실행별 평균과 다른 분모입니다.</p>`}$('compareContent').innerHTML=html}
function distributionSVG(values,d){if(!values.length)return'<div class="empty-state">유효한 실행별 값이 없습니다.</div>';const w=700,h=115,L=40,R=35,min=Math.min(...values.map(r=>r.value)),max=Math.max(...values.map(r=>r.value)),pad=(max-min)*.1||Math.max(Math.abs(max)*.05,.05),lo=min-pad,hi=max+pad,X=v=>L+(v-lo)/(hi-lo)*(w-L-R);let s=`<svg viewBox="0 0 ${w} ${h}" width="100%" height="100%" role="img" aria-label="실행별 실제 값 분포"><path d="M${L} 62H${w-R}" stroke="#cfdbe3"/>`;values.forEach((r,i)=>{const y=44+(i%3)*9;s+=`<circle cx="${X(r.value)}" cy="${y}" r="5" fill="#168f8b" fill-opacity=".65" stroke="#fff"><title>${esc(r.label)}: ${esc(fmt(r.value,d.unit,3))}</title></circle>`});for(let i=0;i<5;i++){const v=lo+(hi-lo)*i/4;s+=`<path d="M${X(v)} 62v5" stroke="#cfdbe3"/><text x="${X(v)}" y="84" text-anchor="middle" font-size="9" fill="#8295a5">${esc(fmt(v,d.unit,2))}</text>`}return s+`<text x="${L}" y="106" font-size="9" fill="#8295a5">각 점은 저장된 한 실행입니다 · ${values.length}개 표본 · 정규분포 가정 없음</text></svg>`}
async function share(){if(!S.manifest)return toast('먼저 실행 결과를 선택하세요.');const limited=S.window[1]-S.window[0]>240,shareWindow=limited?[Math.max(0,S.time-120),Math.min(S.duration,S.time+120)]:[...S.window];$('shareButton').disabled=true;if(limited)toast(`선택 시각 전후 최대 120초 스냅샷을 만듭니다 (${num(shareWindow[0],1)}–${num(shareWindow[1],1)} s).`);try{const data=await api('/api/share',{method:'POST',body:JSON.stringify({run_id:S.runId,revision:S.revision,time_s:S.time,window_s:shareWindow,selected_event_id:S.eventId,selected_node:S.node,selected_peer:S.peer,format:'snapshot'})});if(data.download_url){const a=document.createElement('a');a.href=data.download_url;a.download='';a.click();toast(`${limited?'선택 시각 전후 최대 120초 ':''}공유 스냅샷을 만들었습니다 (${num(shareWindow[0],1)}–${num(shareWindow[1],1)} s). 자동 갱신되지 않습니다.`)}else toast(data.message||'공유 파일을 만들었습니다.')}catch(error){toast(error.message)}finally{$('shareButton').disabled=false}}
async function jobs(){try{const result=await api('/api/jobs');const list=result.jobs||[];$('jobsContent').innerHTML=`<div class="notice ${result.enabled?'':'error'}">${result.enabled?'명시적으로 허용된 기존 명령만 실행합니다. 장시간 학습은 이 화면에서 시작하지 않습니다.':'실행 제어가 비활성화되어 있습니다. 완료 결과 조회·재생·자동 등록은 계속 사용할 수 있습니다.'}</div><div class="job-controls"><select id="jobCommand"><option value="verify">저장 추론 경로 검증</option><option value="doctor">연구 환경 점검</option><option value="evaluate">기존 체크포인트 1사례 평가</option></select><input id="jobCheckpoint" placeholder="기존 checkpoint 상대 경로 (평가 시)" style="min-width:240px;border:1px solid #dfe7ed;padding:7px;border-radius:5px"><button class="button primary" id="jobStart" ${result.enabled?'':'disabled'}>연구 작업 시작</button></div><div>${list.length?list.map(j=>`<div class="job-row"><div><strong>${esc(j.command||j.kind||'작업')}</strong> · ${esc(j.status||j.process_status)}<small>${esc(j.job_id||j.id)} · ${esc(dateText(j.started_at))}</small></div><button class="button compact" data-job-stop="${esc(j.job_id||j.id)}" ${['running','starting'].includes(j.status||j.process_status)?'':'disabled'}>이 작업 중지</button></div>`).join(''):'<div class="empty-state">대시보드가 시작한 작업이 없습니다.</div>'}</div>`;$('jobStart').onclick=async()=>{const command=$('jobCommand').value,checkpoint=$('jobCheckpoint').value.trim();if(!confirm(`연구 환경에서 '${command}' 작업을 시작합니다. 저장 로그 재생과 별개입니다. 계속하시겠습니까?`))return;try{await api('/api/jobs',{method:'POST',body:JSON.stringify({command,...(checkpoint?{checkpoint}:{}),cases:1,workers:1})});await jobs()}catch(e){toast(e.message)}};$('jobsContent').querySelectorAll('[data-job-stop]').forEach(b=>b.onclick=async()=>{try{await api(`/api/jobs/${encodeURIComponent(b.dataset.jobStop)}/stop`,{method:'POST',body:'{}'});await jobs()}catch(e){toast(e.message)}})}catch(error){$('jobsContent').innerHTML=`<div class="empty-state">실행 관리 서비스를 사용할 수 없습니다.<br>${esc(error.message)}</div>`}}
$('runList').addEventListener('click',e=>{const check=e.target.closest('[data-compare]');if(check){e.stopPropagation();const row=S.runs.find(r=>r.run_id===check.dataset.compare&&revisionOf(r)===check.dataset.revision);if(check.checked)S.compare.set(row.run_id,revisionOf(row));else S.compare.delete(row.run_id);renderRuns();$('compareSelectionCount').textContent=`${S.compare.size}개 실행 선택`;if(S.tab==='compare')loadComparison();return}if(e.target.closest('.run-history'))return;const card=e.target.closest('[data-run]');if(card)selectRun(card.dataset.run,card.dataset.revision)});$('runList').addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.matches('[role="button"][data-run]'))selectRun(e.target.dataset.run,e.target.dataset.revision)});
$('runList').addEventListener('change',e=>{const select=e.target.closest('[data-revision-select]');if(!select)return;const id=select.dataset.revisionSelect;if(S.compare.has(id))S.compare.set(id,select.value);selectRun(id,select.value)});
$('libraryScope').onchange=()=>{$('flightFilter').value='v21';for(const id of ['provinceFilter','districtFilter','performanceFilter','regionFilter','modeFilter','splitFilter','missionFilter','kindFilter','directionFilter','codeFilter','seedFilter','searchInput'])$(id).value='';fillFilters();renderRuns();const first=filterRuns()[0];if(first&&!filterRuns().some(r=>r.run_id===S.runId))selectRun(first.run_id,revisionOf(first));};
$('performanceRefresh').onclick=loadPerformance;
$('searchInput').addEventListener('input',()=>{if(isDemoLibrary())applyDemoFilters();else renderRuns()});
$('sortOrder').onchange=()=>renderRuns();
for(const id of ['provinceFilter','districtFilter','flightFilter','performanceFilter']){
 $(id).addEventListener('change',()=>{
  if(id==='provinceFilter'){$('districtFilter').value='';fillGeographyFilters()}
  applyDemoFilters();
 });
}
for(const id of ['regionFilter','modeFilter','splitFilter','missionFilter','kindFilter','directionFilter','codeFilter','seedFilter'])$(id).addEventListener(id.includes('Input')||id==='seedFilter'?'input':'change',()=>{renderRuns();if(S.follow){const latest=latestEligible();if(latest&&latest.run_id!==S.runId)selectRun(latest.run_id)}});
$('refreshButton').onclick=()=>refreshRuns(false,true);$('followLatest').onchange=()=>{S.follow=$('followLatest').checked;$('followStateBadge').textContent=S.follow?'켜짐':'기본 OFF';$('followStateBadge').className='tag'+(S.follow?' teal':'');if(S.follow){const latest=latestEligible();if(latest)selectRun(latest.run_id);else toast('현재 필터에 실제 완료시각이 기록된 검증 결과가 없습니다. 과거 결과의 수정시각은 최신 기준으로 사용하지 않습니다.')}};document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));$('playButton').onclick=togglePlayback;$('stepBack').onclick=()=>{S.playing=false;setTime(S.time-.5)};$('stepForward').onclick=()=>{S.playing=false;setTime(S.time+.5)};$('timeSlider').oninput=e=>{S.playing=false;setTime(e.target.value)};$('timeInput').oninput=e=>{S.playing=false;setTime(e.target.value)};$('timeInput').onchange=e=>{S.playing=false;setTime(e.target.value)};$('timeInput').onkeydown=e=>{if(e.key==='Enter'){S.playing=false;setTime(e.target.value);e.target.blur()}};$('speedSelect').onchange=e=>S.speed=Number(e.target.value);$('fitMap').onclick=()=>renderer.reset();document.querySelectorAll('[data-layer]').forEach(el=>el.onchange=()=>{S.layers[el.dataset.layer]=el.checked;renderAnalysis()});$('seriesSelect').onchange=renderCharts;$('fullWindow').onclick=()=>{S.window=[0,S.duration];renderCharts()};$('metricChart').onclick=e=>{const rect=$('metricChart').getBoundingClientRect();const ratio=Math.max(0,Math.min(1,((e.clientX-rect.left)/rect.width-48/720)/(1-66/720)));S.playing=false;setTime(S.window[0]+ratio*(S.window[1]-S.window[0]))};for(const id of ['nodeSelect','peerSelect'])$(id).onchange=()=>{S.pairSelectionVersion++;S.node=$('nodeSelect').value;S.peer=$('peerSelect').value;syncSelectionControls();S.obsWindow=null;S.observations=[];renderAnalysis();requestObservations()};$('eventTypeFilter').onchange=()=>{S.eventLimit=180;renderEvents()};$('eventList').onclick=e=>{if(e.target.closest('[data-event-more]')){S.eventLimit+=180;renderEvents();return}const b=e.target.closest('[data-event]');if(b)selectEvent(b.dataset.event)};$('clearEvent').onclick=()=>{S.eventId=null;S.window=[0,S.duration];$('eventContext').classList.add('hidden');$('mapHeading').textContent='비행 경로와 네트워크';renderEvents();renderAnalysis()};$('compareRefresh').onclick=loadComparison;$('compareMetric').onchange=loadComparison;$('shareButton').onclick=share;$('jobsOpen').onclick=()=>{$('jobsDialog').showModal();jobs()};$('jobsClose').onclick=()=>$('jobsDialog').close();
let last=0,lastDraw=0;function tick(now){if(S.playing&&last&&now-lastDraw>90){const dt=Math.min((now-lastDraw)/1000,.5)*S.speed;S.time=Math.min(S.duration,S.time+dt);if(S.time>=S.duration)S.playing=false;setTime(S.time);lastDraw=now}if(!S.playing)lastDraw=now;last=now;requestAnimationFrame(tick)}requestAnimationFrame(tick);
$('runList').innerHTML=Array.from({length:4},()=>'<div class="skeleton"></div>').join('');refreshRuns(true);setInterval(()=>refreshRuns(),5000);window.addEventListener('online',()=>refreshRuns());window.__SWARM_DEBUG__={state:S,selectRun,setTime,selectEvent,refreshRuns};


document.querySelectorAll('[data-map-view]').forEach(button=>button.onclick=()=>{
 renderer.setViewMode(button.dataset.mapView);
 $('mapCompass').classList.toggle('hidden',button.dataset.mapView==='3d');
 document.querySelectorAll('[data-map-view]').forEach(el=>{const on=el===button;el.classList.toggle('active',on);el.setAttribute('aria-pressed',String(on))});
 $('mapHelp').textContent=button.dataset.mapView==='3d'?'드래그 이동 · Shift+드래그 회전 · 휠 확대':'드래그 이동 · 휠 확대';
 renderer.syncHelp?.();
});
let expandedMapState=null;
function syncExpandedButton(){const panel=$('mapPanel');$('expandMap').textContent=document.fullscreenElement===panel||panel.classList.contains('bm-expanded')?'크게 보기 종료':'크게 보기'}
function setMapExpanded(on){
 const panel=$('mapPanel');
 if(on&&!expandedMapState){expandedMapState={overflow:document.body.style.overflow,x:window.scrollX,y:window.scrollY,panelScroll:panel.scrollTop,focus:document.activeElement};document.body.style.overflow='hidden';document.body.classList.add('bm-map-expanded');panel.classList.add('bm-expanded');$('expandMap').focus()}
 else if(!on&&expandedMapState){const previous=expandedMapState;expandedMapState=null;panel.classList.remove('bm-expanded');document.body.classList.remove('bm-map-expanded');document.body.style.overflow=previous.overflow;panel.scrollTop=previous.panelScroll;window.scrollTo(previous.x,previous.y);previous.focus?.focus?.({preventScroll:true})}
 syncExpandedButton();requestAnimationFrame(()=>renderer.draw());
}
$('expandMap').onclick=async()=>{
 const panel=$('mapPanel'),visual=!!visualMapConfig(mapKey(S.manifest))?.map_chunk;
 if(panel.classList.contains('bm-expanded')){setMapExpanded(false);return}
 try{if(document.fullscreenElement===panel)await document.exitFullscreen();else if(panel.requestFullscreen)await panel.requestFullscreen();else if(visual)setMapExpanded(true);else toast('이 브라우저에서는 전체 화면을 지원하지 않습니다.')}
 catch{if(visual)setMapExpanded(true);else toast('전체 화면을 열지 못했습니다. 브라우저 설정을 확인하세요.')}
};
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&expandedMapState&&!document.querySelector('dialog[open]')){event.preventDefault();setMapExpanded(false)}});
document.addEventListener('fullscreenchange',()=>{syncExpandedButton();requestAnimationFrame(()=>renderer.draw())});

installPlaybackShortcuts({
 enabled:()=>!!visualMapConfig(mapKey(S.manifest))?.map_chunk,
 blocked:()=>[...document.querySelectorAll('dialog[open]')].some(d=>d.id!=='incidentDialog'),
 incidentActive:()=>!!$('incidentDialog')?.open,
 toggleIncident:()=>incidentReplay.togglePlayback(),
 mainActive:()=>S.tab==='map'&&!$('analysisView')?.classList.contains('hidden'),
 toggleMain:togglePlayback
});
window.__PLAYBACK_TEST__={snapshot:()=>({region:mapKey(S.manifest),tab:S.tab,time:S.time,playing:S.playing,incident:incidentReplay.snapshot()}),toggleMain:togglePlayback,incident:incidentReplay};
