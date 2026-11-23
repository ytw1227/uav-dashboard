// Reuse the lossless observation chunks. No second copy of all RF samples is
// embedded for incident graphs. This matches plotly_figures.read_signals.
const finite=Number.isFinite;
const buffer=new ArrayBuffer(8),bits=new DataView(buffer);
export function roundFour(value){
 if(!finite(value)||value===0)return value;
 const sign=value<0?-1:1;
 bits.setFloat64(0,Math.abs(value),false);
 const hi=bits.getUint32(0,false),lo=bits.getUint32(4,false),exponent=(hi>>>20)&2047;
 const significand=(BigInt(hi&0xfffff)<<32n)|BigInt(lo);
 let numerator=(exponent?significand+(1n<<52n):significand)*10000n;
 const shift=(exponent?exponent-1023:-1022)-52;
 if(shift>=0)return sign*Number(numerator<<BigInt(shift))/10000;
 const denominator=1n<<BigInt(-shift),whole=numerator/denominator,remainder=numerator%denominator;
 const twice=remainder*2n,rounded=whole+(twice>denominator||(twice===denominator&&whole%2n)?1n:0n);
 return sign*Number(rounded)/10000;
}
export function incidentSignalWindow(events,event){
 const windows=[];
 for(const row of [...events].sort((a,b)=>a.start_s-b.start_s)){
  const [lower,upper]=row.window,last=windows.at(-1);
  if(last&&lower<=last[1]+.5)last[1]=Math.max(last[1],upper);else windows.push([lower,upper]);
 }
 return windows.find(([lower,upper])=>lower<=event.window[0]&&upper>=event.window[1])||event.window;
}
export function observationsToIncidentSignal(rows,lower,upper){
 const output={t:[],m:[],age:[],sinr:[],power:[],new:[]};let last=null;
 const sorted=[...new Map(rows.map(row=>[row.available_at_s,row])).values()].sort((a,b)=>a.available_at_s-b.available_at_s);
 for(const row of sorted){
  const t=row.available_at_s;if(t<Math.max(0,lower-.5)||t>upper)continue;
  // Portable RF rows retain this same source age under each beacon field.
  // Require matching recorded ages; a missing/invalid age remains unavailable.
  const hasAgeField=Object.hasOwn(row.valid||{},'beacon_age_s');
  const sinrAge=row.ages?.beacon_received_sinr_db,powerAge=row.ages?.beacon_received_power_dbm;
  const age=hasAgeField?(row.valid.beacon_age_s?row.values?.beacon_age_s:null):(sinrAge===powerAge?sinrAge:null);
  const validAge=finite(age)&&age>=0,measured=validAge?roundFour(t-age):null;
  const values=['beacon_received_sinr_db','beacon_received_power_dbm'].map(key=>row.valid?.[key]?row.values[key]:null);
  const hasMeasurement=measured!==null&&values.some(value=>value!==null);
  const fresh=hasMeasurement&&(last===null||Math.abs(measured-last)>.0005);
  if(t>=lower){const valuesToAdd=[t,measured,validAge?roundFour(age):null,...values,Number(fresh)];Object.keys(output).forEach((key,index)=>output[key].push(valuesToAdd[index]));}
  if(hasMeasurement)last=measured;
 }
 output.traces={};
 for(const [key,label,unit] of [['power','비콘 수신전력','dBm'],['sinr','비콘 SINR','dB']]){
  const indices=output.new.map((fresh,index)=>fresh&&output.m[index]!==null&&output[key][index]!==null?index:-1).filter(index=>index>=0);
  output.traces[key]={type:'scatter',x:indices.map(i=>output.m[i]),y:indices.map(i=>output[key][i]),customdata:indices.map(i=>output.t[i]),name:label,mode:'markers',marker:{color:'#08768c',size:6,line:{color:'#ffffff',width:.5}},connectgaps:false,meta:{role:'measurement',unit},hovertemplate:'측정 %{x:.2f}초 · 기록 %{customdata:.1f}초<br>%{y:.2f} '+unit+'<extra></extra>'};
 }
 return output;
}
async function query(url){
 if(globalThis.__PORTABLE__)return globalThis.__PORTABLE__.api(url);
 const response=await fetch(url);if(!response.ok)throw Error('통신 관측을 열 수 없습니다.');return response.json();
}
export async function loadIncidentSignals(run,book,event,isCurrent=()=>true){
 const [lower,upper]=incidentSignalWindow(book.events,event),pairs={},fleet=run.manifest.fleet||[];
 const directions=fleet.flatMap(receiver=>fleet.filter(sender=>sender.id!==receiver.id).map(sender=>[receiver.id,sender.id]));
 // The portable reader caches four directions. Bound parallel decoding as well.
 for(let index=0;index<directions.length;index+=4){
  if(!isCurrent())return null;
  await Promise.all(directions.slice(index,index+4).map(async([receiver,sender])=>{
   const params=new URLSearchParams({revision:book.revision,observer:receiver,peer:sender,from:Math.max(0,lower-.5),to:upper,at:lower,channels:'observations'});
   const data=await query(`/api/runs/${encodeURIComponent(run.id)}/timeline?${params}`);
   if(!isCurrent())return;
   const rows=[...(data.observations||[]),...(data.at?.observations||[])].filter(row=>row.observer_id===receiver&&row.peer_id===sender);
   pairs[receiver+'|'+sender]=observationsToIncidentSignal(rows,lower,upper);
  }));
 }
 return isCurrent()?pairs:null;
}
