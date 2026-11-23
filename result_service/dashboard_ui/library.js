import {DEMO_MAPS} from './geography.js';
export const REPRESENTATIVE_MAPS=DEMO_MAPS.map(map=>map.region);
export const MAP_LABELS=Object.fromEntries(DEMO_MAPS.map(map=>[map.region,map.label]));
export const CORE_RF=['last_ack_sinr_db','beacon_received_sinr_db','last_ack_received_power_dbm','beacon_received_power_dbm','ack_success_ratio_in_interval'];
export const CORE_METRICS=['search_connected_mean','pdr','information_shared_fraction','outage_total_s','outage_max_s','outage_events','relay_travel_m'];
export const FINAL_CORE_METRICS=['search_connected_mean','timely_pdr','information_shared_fraction','persistent_cut_seconds','persistent_max_s','persistent_onsets','relay_travel_m'];
export const rowMetadata=r=>({...r,...(r?.experiment_settings||{}),...(r?.metadata||{})});
export const revisionOfRow=r=>r?.bundle_revision||r?.revision;
export const mapKey=r=>{const m=rowMetadata(r);return m.region||m.map_id||''};
export const mapName=r=>{const m=rowMetadata(r);return MAP_LABELS[mapKey(r)]||m.region_name||m.region||m.map_id||'지도 미기록'};
export const playable=r=>!r.conflict&&(!r.artifact_kind||r.artifact_kind==='episode')&&(r.export_status||r.status?.export_status||'ready')==='ready'&&!!revisionOfRow(r);
const stamp=v=>Number.isFinite(Date.parse(v))?Date.parse(v):0;
// Conversion time chooses a display revision; it is never a research completion time.
export function representativeRuns(rows){
 const byId=new Map();
 for(const row of rows.filter(playable)){const list=byId.get(row.run_id)||[];list.push(row);byId.set(row.run_id,list)}
 return [...byId.values()].map(versions=>{versions.sort((a,b)=>stamp(b.exported_at)-stamp(a.exported_at)||String(revisionOfRow(b)).localeCompare(String(revisionOfRow(a))));return {...versions[0],versions}});
}
export function mapGroups(rows){
 const groups=new Map();
 for(const row of rows){const m=rowMetadata(row),id=mapKey(row),hash=m.map_hash;const key=id+'|'+(hash||'unknown:'+row.run_id);if(!groups.has(key))groups.set(key,{key,id,hash,name:mapName(row),runs:[]});groups.get(key).runs.push(row)}
 return [...groups.values()].sort((a,b)=>{const ai=REPRESENTATIVE_MAPS.indexOf(a.id),bi=REPRESENTATIVE_MAPS.indexOf(b.id);return (ai<0?99:ai)-(bi<0?99:bi)||a.name.localeCompare(b.name)||a.key.localeCompare(b.key)});
}
export function isFinalV1(row){const m=rowMetadata(row);return m.adapter_id==='final-v1'||m.research_version==='Final V1'}
export function coreMetrics(row){return isFinalV1(row)?FINAL_CORE_METRICS:CORE_METRICS}
export function isRepresentative(row){
 const m=rowMetadata(row);
 return isFinalV1(row) && ['v21','nominal'].includes(m.mode) &&
  m.split==='demonstration' && REPRESENTATIVE_MAPS.includes(mapKey(row));
}

// A map name alone does not identify a matched experiment.
export function sameFlightConditions(first,second){
 const a=rowMetadata(first), b=rowMetadata(second);
 return ['region','map_hash','kind','direction','seed'].every(key =>
  a[key] !== null && a[key] !== undefined && a[key] !== '' && a[key] === b[key]);
}

export function pairedFlights(rows,selected){
 if(!isRepresentative(selected))return null;
 const mode=rowMetadata(selected).mode;
 const eligible=representativeRuns(rows).filter(isRepresentative);
 if(mode==='nominal'){
  const {paired_v21_run_id:link,paired_v21_revision:revision}=rowMetadata(selected);
  const v21=rows.find(row => row.run_id===link && revisionOfRow(row)===revision &&
   playable(row) && isRepresentative(row) && rowMetadata(row).mode==='v21' && sameFlightConditions(row,selected));
  return {nominal:selected,v21:v21||null};
 }
 const nominal=eligible.filter(row => rowMetadata(row).mode==='nominal' &&
  rowMetadata(row).paired_v21_run_id===selected.run_id &&
  rowMetadata(row).paired_v21_revision===revisionOfRow(selected) && sameFlightConditions(selected,row));
 return {nominal:nominal.length===1?nominal[0]:null,v21:selected};
}
export function scopedRuns(rows,scope='representative'){
 const episodes=representativeRuns(rows);
 const baselines=episodes.filter(row => isRepresentative(row) && rowMetadata(row).mode==='nominal');
 return episodes.filter(row => scope==='all'||(scope==='representative'?isRepresentative(row):!isRepresentative(row)))
  .map(row => {
   if(!isRepresentative(row) || rowMetadata(row).mode!=='v21')return row;
   const references=baselines.filter(base => rowMetadata(base).paired_v21_run_id===row.run_id);
   if(references.length!==1)return row;
   const base=references[0], pinned=row.versions.find(version =>
    revisionOfRow(version)===rowMetadata(base).paired_v21_revision && sameFlightConditions(version,base));
   return pinned ? {...pinned,versions:row.versions} : row;
  });
}

export function missionStatus(r){
 const m=rowMetadata(r),completed=m.mission_completed??r?.final_metrics?.mission_completed??r?.metrics?.mission_completed;
 const status=m.mission_status||r?.status?.mission_status||(completed===true?'success':completed===false?'failure':'not_assessed');
 return ({completed:'success',not_completed:'failure',failed:'failure'})[status]||status;
}

// Count each map once, even when a flight was converted again or rerun.
// Prefer a fully matched pair and keep the V21 revision pinned by its nominal flight.
export function demoMapRecords(rows){
 const latest=representativeRuns(rows).filter(isRepresentative)
  .sort((a,b)=>stamp(b.exported_at)-stamp(a.exported_at)||String(revisionOfRow(b)).localeCompare(String(revisionOfRow(a))));
 const records=[];
 for(const geography of DEMO_MAPS){
  const regionRows=latest.filter(row=>mapKey(row)===geography.region);
  if(!regionRows.length)continue;
  const nominal=regionRows.filter(row=>rowMetadata(row).mode==='nominal');
  const paired=nominal.map(row=>pairedFlights(rows,row)).find(pair=>pair?.v21);
  // An unmatched baseline and V21 must never contribute a made-up comparison.
  const single=regionRows[0];
  records.push({...geography,...(paired||{[rowMetadata(single).mode]:single})});
 }
 return records;
}

export const DEMO_METRICS={pdr:'PDR',search_connected_mean:'CTR'};

export function demoRatio(row,key){
 const value=row?.metrics?.[key]??row?.final_metrics?.[key];
 return Number.isFinite(value)&&value>=0&&value<=1 ? value : null;
}

export function filterDemoMaps(records,{province='',district='',mode='v21',metric='',search=''}={}){
 const query=search.trim().toLowerCase();
 const selected=records.filter(map=>
  (!province||map.province===province)&&(!district||map.district===district)&&
  (!query||[map.label,map.province,map.district,map.region].join(' ').toLowerCase().includes(query)));
 const visible=[], above=[], below=[], missing=[];
 const hits={nominal:0,v21:0};
 for(const map of selected){
  if(!metric){if(map[mode])visible.push(map);continue}
  const nominal=demoRatio(map.nominal,metric), v21=demoRatio(map.v21,metric);
  if(nominal===null||v21===null){missing.push(map);continue}
  const baseLow=nominal<=0.95, appliedLow=v21<=0.95;
  if(baseLow)hits.nominal++;
  if(appliedLow)hits.v21++;
  if(baseLow&&!appliedLow)above.push(map);
  if(!baseLow&&appliedLow)below.push(map);
  if(mode==='nominal'?baseLow:appliedLow)visible.push(map);
 }
 return {selected,visible,hits,above,below,missing,validCount:selected.length-missing.length};
}


// UI labels do not override recorded units or availability clocks.
export const TIME_SERIES_COPY={
 search_connected:{label:'수색망 연결 상태',description:'수색 드론 모두가 연결되면 1, 끊기면 0입니다. 중계 경로도 포함합니다.'},
 search_pair_reachability:{label:'수색 드론 쌍의 통신 도달률',description:'전체 수색 드론 쌍 중 서로 통신 가능한 쌍의 비율입니다. 중계 경로도 포함합니다.'},
 timely_pdr_so_far:{label:'기한 내 패킷 전달률',description:'현재까지 생성된 패킷 중 0.25초 안에 전달된 비율입니다. 종료 후 정산 전 기록입니다.'},
 pdr_so_far:{label:'누적 패킷 전달률',description:'현재까지 생성된 패킷 중 전달된 비율입니다. 종료 후 정산 전 기록입니다.'},
 information_shared_fraction:{label:'공통 공유율',description:'전체 수색 대상 중 모든 수색 드론이 공통으로 정보를 가진 비율입니다.'}
};
export function coreTimeSeriesKeys(row,available){
 const keys=new Set(available);
 const packet=isFinalV1(row)&&keys.has('timely_pdr_so_far')?'timely_pdr_so_far':'pdr_so_far';
 return ['search_connected','search_pair_reachability',packet,'information_shared_fraction'].filter(k=>keys.has(k));
}
export function timeSeriesDefinition(key,recorded={},fallback={}){
 const copy=TIME_SERIES_COPY[key]||{};
 return {...fallback,...recorded,...copy,scopeLabel:({instant:'현재 상태',cumulative_to_t:'현재까지 누적',interval:'구간 통계'})[recorded.scope]||fallback.scope||recorded.scope||'기록 시각 기준'};
}
