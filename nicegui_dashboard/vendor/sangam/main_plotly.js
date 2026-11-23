import {MapRenderer,finite,positionAt,intervalAt} from './render.js';
import {droneMapLabel} from './map3d.js';
import {commAt,detourText} from './comm_state.js';
import {aircraftPaint,selectedReadout} from './map_status.js';
import {focusPlaybackSurface} from './playback_shortcuts.js';

const clone=value=>JSON.parse(JSON.stringify(value));
const CAMERA={up:{x:0,y:0,z:1},eye:{x:.9,y:-1.25,z:1.65},projection:{type:'orthographic'}};
const TOP={up:{x:0,y:1,z:0},eye:{x:0,y:0,z:2.4},projection:{type:'orthographic'}};
// Apply the accepted presentation interactions to every supplied visual map.
// Exports without a visual scene keep the existing canvas fallback.
const presentationRegion=data=>!!visualMapConfig(data?.region)?.map_chunk;
// Region assets are loaded on demand. The legacy entry remains readable for
// preserved single-region exports, but never supplies another region's map.
export function visualMapConfig(region){return globalThis.__UAV_VISUAL__?.maps?.[region]||(region==='bundang'?globalThis.__BUNDANG_VISUAL__:null)||null}
// Explicit framing controls must recover from a translated or deeply zoomed
// camera, while retaining the user's viewing direction and upright vector.
export function recenteredMainCamera(camera,viewMode='3d'){
 const base=viewMode==='2d'?TOP:CAMERA,source=clone(camera||base),axes=['x','y','z'];
 let direction=axes.map(axis=>(finite(source.eye?.[axis])?source.eye[axis]:base.eye[axis])-(finite(source.center?.[axis])?source.center[axis]:0));
 let length=Math.hypot(...direction);
 if(!finite(length)||length<1e-8){direction=axes.map(axis=>base.eye[axis]);length=Math.hypot(...direction)}
 const distance=Math.hypot(base.eye.x,base.eye.y,base.eye.z);
 return {...source,center:{x:0,y:0,z:0},eye:Object.fromEntries(axes.map((axis,i)=>[axis,direction[i]*distance/length])),up:source.up||clone(base.up)};
}
// A render domain describes all available geometry, not the camera's focus.
// Never derive it from a current frame or a selected event window.
export function computeMainRenderDomain(scene,positions,figure,sharedDomain,options={}){
 const domain={x:[Infinity,-Infinity],y:[Infinity,-Infinity],z:[Infinity,-Infinity]};
 const include=(axis,value)=>{if(finite(value)){domain[axis][0]=Math.min(domain[axis][0],value);domain[axis][1]=Math.max(domain[axis][1],value)}};
 const bounds=scene?.bounds||figure?.visual_metadata?.bounds;
 if(Array.isArray(bounds)&&bounds.length>=4){include('x',bounds[0]);include('y',bounds[1]);include('x',bounds[2]);include('y',bounds[3])}
 include('z',scene?.coordinate_system?.ground_z_m??figure?.visual_metadata?.coordinate_system?.ground_z_m??0);
 for(const axis of ['x','y','z'])for(const value of sharedDomain?.[axis]||[])include(axis,value);
 for(const row of positions||[])for(const node of row.uavs||[])for(const axis of ['x','y','z'])include(axis,node[axis]);
 for(const trace of figure?.data||[]){
  if(!trace.meta?.visual_static)continue;
  if(options.includeReference===false&&['groundPlane','groundGrid'].includes(trace.meta.role))continue;
  for(const axis of ['x','y','z'])for(const value of trace[axis]||[])include(axis,value);
 }
 for(const axis of ['x','y','z']){
  if(!domain[axis].every(finite))domain[axis]=axis==='z'?[0,60]:[-200,200];
  if(domain[axis][1]===domain[axis][0])domain[axis][1]=domain[axis][0]+1;
  if(options.pad!==false){const margin=Math.max(axis==='z'?2:5,(domain[axis][1]-domain[axis][0])*.02);domain[axis]=[domain[axis][0]-margin,domain[axis][1]+margin]}
 }
 return domain;
}
export function metricMainAspect(domain,scale=1){
 const y=domain.y[1]-domain.y[0];return{x:(domain.x[1]-domain.x[0])/y*scale,y:scale,z:(domain.z[1]-domain.z[0])/y*scale};
}
export function mainDomainCorners(domain){
 const result=[];for(const x of domain.x)for(const y of domain.y)for(const z of domain.z)result.push({x,y,z});return result;
}
export function insideMainRenderDomain(point,domain){return ['x','y','z'].every(axis=>finite(point[axis])&&point[axis]>=domain[axis][0]&&point[axis]<=domain[axis][1])}
// Project through the bundled Plotly engine's actual view matrices. This is
// guarded because it is also used only as a visual framing check, never data.
export function projectedMainFleet(host,nodes){
 const scene=host?._fullLayout?.scene?._scene,gl=scene?.glplot,params=gl?.cameraParams;
 const canvas=gl?.canvas||host?.querySelector?.('.gl-canvas'),box=canvas?.getBoundingClientRect?.(),hostBox=host?.getBoundingClientRect?.();
 if(!params||!box?.width||!box?.height||!scene.dataScale||!hostBox)return null;
 const matrices=[params.model,params.view,params.projection];
 if(matrices.some(m=>!m||m.length!==16))return null;
 const multiply=(m,v)=>Array.from({length:4},(_,r)=>m[r]*v[0]+m[r+4]*v[1]+m[r+8]*v[2]+m[r+12]*v[3]);
 return nodes.map(n=>{let v=[n.x*scene.dataScale[0],n.y*scene.dataScale[1],(finite(n.z)?n.z:0)*scene.dataScale[2],1];for(const matrix of matrices)v=multiply(matrix,v);
  return {id:n.id,x:box.left-hostBox.left+(v[0]/v[3]+1)*box.width/2,y:box.top-hostBox.top+(1-v[1]/v[3])*box.height/2,depth:v[2]/v[3]};});
}
const line=(role,name,color,width=3,dash='solid')=>({type:'scatter3d',mode:'lines',name,meta:{role},x:[],y:[],z:[],customdata:[],hoverinfo:'skip',connectgaps:false,line:{color,width,dash}});
const finiteVector=v=>Array.isArray(v)&&v.length>=2&&v.every(finite);
const coords=n=>[n.x,n.y,finite(n.z)?n.z:0];
const segment=(trace,a,b,detail=null)=>{for(let k=0;k<3;k++)trace[['x','y','z'][k]].push(a[k],b[k],null);trace.customdata.push(detail,detail,null)};
function dynamics(){
 return [
  {type:'scatter3d',mode:'markers',name:'기체의 지상 투영',meta:{role:'groundPins'},x:[],y:[],z:[],customdata:[],hoverinfo:'skip',marker:{size:2,color:'#627f94',opacity:.3}},
  line('altitudeStems','기록 고도','#537486',1,'dot'),line('otherTracks','기체 궤적','#526a80',2),
  line('selectedTrack','선택 기체 궤적','#a6d9ff',4),line('availableLinks','가용 링크','#477e93',2),
  line('selectedLink','선택한 두 기체의 연결','#64efff',7),line('lostLink','사용할 수 없는 직접 링크','#ff667b',6,'dash'),
  line('requestedCommand','요청 보정','#ffc367',3,'dash'),line('appliedCommand','적용 보정','#d79aff',4),
  line('actualMovement','기록으로 확인한 실제 변위','#c1e0ff',6),
  {type:'scatter3d',mode:'markers',name:'선택한 기체',meta:{role:'selectedHalo'},x:[],y:[],z:[],customdata:[],hoverinfo:'skip',marker:{size:12,symbol:'circle-open',color:'#e6fbff',line:{color:'#e6fbff',width:1.5}}},
  {type:'scatter3d',mode:'markers',name:'드론',meta:{role:'drones'},x:[],y:[],z:[],customdata:[],text:[],hovertext:[],
   marker:{size:6.25,symbol:'circle',color:[],line:{color:'#f2fbff',width:.85}},
   hovertemplate:'<b>%{text}</b> · %{hovertext}<br>동 %{x:.1f} m · 북 %{y:.1f} m<br>고도 %{z:.1f} m<extra></extra>'}
 ];
}
function arrow(trace,n,v,id){
 if(!n||!finiteVector(v)||Math.hypot(...v)===0)return;
 const a=coords(n),b=[a[0]+v[0],a[1]+v[1],a[2]+(v[2]||0)];segment(trace,a,b,id);
 const dx=b[0]-a[0],dy=b[1]-a[1],distance=Math.hypot(dx,dy);
 if(distance<.001)return;
 const wing=Math.min(distance*.35,5)/distance;
 for(const side of [-.6,.6])segment(trace,b,[b[0]-wing*(dx+side*dy),b[1]-wing*(dy-side*dx),b[2]],id);
}

// Only presentation data are derived here. Coordinates and action availability
// follow the same recorded/interpolated inputs used by the existing inspector.
export function mainVisualLayers(data){
 const traces=dynamics(),byRole=Object.fromEntries(traces.map(t=>[t.meta.role,t]));
 for(const role of ['availableLinks','selectedLink','lostLink'])byRole[role].hoverinfo='name';
 byRole.actualMovement.meta.recorded_available_at_s=[];
 byRole.actualMovement.meta.source_decision_time_s=null;
 byRole.actualMovement.meta.explicit_future=!!data.layers?.future;
 const nodes=(data.position?.uavs||[]).filter(n=>finite(n.x)&&finite(n.y));
 const lookup=new Map(nodes.map(n=>[n.id,n])),fleet=data.fleet||nodes,ground=data.scene?.coordinate_system?.ground_z_m??0;
 const selected=new Set([data.selectedNode,data.selectedPeer]);
 for(const n of nodes){
  const p=coords(n),trace=byRole.drones;
  for(let k=0;k<3;k++)trace[['x','y','z'][k]].push(p[k]);
  trace.customdata.push(n.id);trace.text.push(droneMapLabel(n.id,fleet));
  const paint=aircraftPaint(data.communication,n.id);
  trace.hovertext.push((n.role==='search'?'수색 드론':n.role==='relay'?'중계 드론':'드론')+' · '+paint.description);
  trace.marker.color.push(paint.color);
  const pin=byRole.groundPins;pin.x.push(p[0]);pin.y.push(p[1]);pin.z.push(ground+.03);pin.customdata.push(n.id);
  if(finite(n.z))segment(byRole.altitudeStems,[p[0],p[1],ground],p,n.id);
  if(n.id===data.selectedNode){const ring=byRole.selectedHalo;ring.x.push(p[0]);ring.y.push(p[1]);ring.z.push(p[2]);ring.customdata.push(n.id)}
 }
 const layers=data.layers||{},t=data.time??0;
 if(layers.tracks)for(const n of nodes){
  const tr=n.id===data.selectedNode?byRole.selectedTrack:byRole.otherTracks;let prev=null;
  for(const row of data.positions||[]){
   if(!layers.future&&row.sample_time_s>t)break;
   const next=row.uavs?.find(u=>u.id===n.id);if(!next){prev=null;continue}
   if(prev)segment(tr,coords(prev),coords(next),n.id);prev=next;
  }
  if(prev&&!layers.future)segment(tr,coords(prev),coords(n),n.id);
 }
 let pair=null;
 for(const edge of data.frame?.links||[]){
  const a=lookup.get(edge.source),b=lookup.get(edge.target);if(!a||!b)continue;
  const chosen=(edge.source===data.selectedNode&&edge.target===data.selectedPeer)||(edge.target===data.selectedNode&&edge.source===data.selectedPeer);
  if(chosen){pair=edge;segment(edge.available?byRole.selectedLink:byRole.lostLink,coords(a),coords(b),[edge.source,edge.target])}
  else if(layers.links&&edge.available)segment(byRole.availableLinks,coords(a),coords(b),[edge.source,edge.target]);
 }
 if(layers.actions&&data.decision){
  const origin=positionAt(data.positions||[],data.decision.decision_time_s);
  for(const a of data.decision.actions||[]){
   const n=origin.uavs.find(n=>n.id===a.uav_id);
   arrow(byRole.requestedCommand,n,a.requested_correction,a.uav_id);
   arrow(byRole.appliedCommand,n,a.applied_correction,a.uav_id);
   if(layers.future){arrow(byRole.actualMovement,n,a.actual_displacement,a.uav_id);byRole.actualMovement.meta.recorded_available_at_s.push(a.actual_available_at_s??a.interval_end_s);byRole.actualMovement.meta.source_decision_time_s=data.decision.decision_time_s}
  }
 }
 if(layers.actions&&!layers.future&&data.completedDecision){
  const origin=positionAt(data.positions||[],data.completedDecision.decision_time_s);
  for(const a of data.completedDecision.actions||[]){
   const available=a.actual_available_at_s??a.interval_end_s;
   if(!finite(available)||available>t)continue;
   arrow(byRole.actualMovement,origin.uavs.find(n=>n.id===a.uav_id),a.actual_displacement,a.uav_id);
   byRole.actualMovement.meta.recorded_available_at_s.push(available);byRole.actualMovement.meta.source_decision_time_s=data.completedDecision.decision_time_s;
  }
 }
 const annotations=nodes.filter(n=>selected.has(n.id)).map(n=>({x:n.x,y:n.y,z:finite(n.z)?n.z:0,text:'<b>'+droneMapLabel(n.id,fleet)+'</b>',showarrow:true,
  // Separate the two labels in screen space. The leader terminates at the
  // unchanged recorded position; aircraft coordinates are never displaced.
  ax:n.id===data.selectedNode?-29:29,ay:n.id===data.selectedNode?-36:36,
  arrowhead:0,arrowwidth:1.2,arrowcolor:aircraftPaint(data.communication,n.id).color,bgcolor:'#102b3e',bordercolor:aircraftPaint(data.communication,n.id).color,borderwidth:1,borderpad:5,
  font:{size:13,color:'#f3fbff',family:'Arial, Malgun Gothic, sans-serif'},captureevents:false}));
 return {traces,annotations,pair,nodes};
}

export class UavMapRenderer{
 constructor(canvas,onSelect){
  this.legacy=new MapRenderer(canvas,onSelect);this.canvas=canvas;this.wrap=canvas.parentElement;this.onSelect=onSelect;
  this.viewMode='3d';this.data=null;this.active=false;this.ready=false;this.busy=false;this.dirty=false;this.epoch=0;this.renderedTime=null;this.error=null;
  this.follow=false;this.dragmode='pan';this.ghost=false;this.camera=clone(CAMERA);this.fitRequest='all';this.viewScope='all';this.cameraWrite=0;this.manualCamera=false;this.resizePending=true;this.projectedPoints=null;this.focusScale=1;this.renderDomain=null;this.fullMapFocus=null;this.domainInsideIds=[];
  this.gestureHeld=false;this.pointerActive=false;this.interactionEpoch=0;this.wheelTimer=null;this.fitInterrupted=false;
   this.presentation=false;this.followZoom='fixed';this.followTick=0;this.followOutsideSince=0;this.followZoomTick=0;this.followInitialFits=0;this.presentationFrames=0;this.annotationLayer=null;this.interactionLayoutPending=false;this.lastDynamicDraw=0;
  this.visualConfig=null;this.visualKey=null;this.plottedKey=null;this.renderedRunId=null;this.needsSceneReset=false;
  this.host=document.createElement('div');this.host.id='bundangMainPlot';this.host.className='bm-plot';this.host.setAttribute('aria-label','건물과 드론의 3D 지도');
  this.wrap.prepend(this.host);
  this.toolbar=document.createElement('div');this.toolbar.className='bm-toolbar';this.toolbar.innerHTML=`<div class="bm-tools-group"><button id="bmFitFleet" title="현재 편대를 한 번 맞춥니다. 자동 추적은 편대 따라보기를 켜세요.">편대 맞춤</button><button id="bmFitAll" title="전체 지도와 두 비행 방식의 기록 범위를 한눈에 봅니다.">전체 지도</button><button id="bmTop" title="북쪽이 위인 수직 시점">위에서</button></div><div class="bm-tools-group"><button id="bmPan" aria-pressed="true">이동</button><button id="bmOrbit" aria-pressed="false">회전</button><button id="bmFollow" aria-pressed="false" title="재생 중 움직이는 편대를 계속 따라봅니다.">편대 따라보기</button><button id="bmGhostBuildings" aria-pressed="false">건물 투명</button></div>`;
  this.wrap.append(this.toolbar);
  this.status=document.createElement('div');this.status.className='bm-status';this.status.innerHTML='<span class="bm-engine">비행과 통신</span><span class="bm-pair"><span class="bm-direct"></span><span class="bm-detour" hidden></span></span><span class="bm-gesture-note" hidden></span>';this.wrap.append(this.status);
  this.recovery=document.createElement('button');this.recovery.className='bm-recovery';this.recovery.id='bmRecover';this.recovery.hidden=true;this.recovery.textContent='편대가 화면 밖에 있습니다 · 다시 보기';this.recovery.onclick=()=>{if(this.presentation)this.enableFollow();else{this.follow=true;this.fit('fleet')}};this.wrap.append(this.recovery);
  this.errorHost=document.createElement('div');this.errorHost.className='bm-error';this.errorHost.hidden=true;this.wrap.append(this.errorHost);
  const byId=id=>this.toolbar.querySelector('#'+id);
  this.legacyFleetFit=byId('bmFitFleet');this.legacyFleetFit.onclick=()=>this.fit('fleet');byId('bmFitAll').onclick=()=>{this.follow=false;this.fit('all')};
  byId('bmTop').onclick=()=>this.setViewMode('2d');
  for(const mode of ['pan','orbit'])byId(mode==='pan'?'bmPan':'bmOrbit').onclick=()=>{this.dragmode=mode;if(mode==='orbit'&&!this.presentation){this.viewScope='manual';this.stopFollowing()}this.interactionLayoutPending=true;this.request();this.syncButtons()};
  byId('bmFollow').onclick=()=>{if(this.presentation){if(this.follow){this.follow=false;this.viewScope='manual';this.manualCamera=true;this.followTick=0;this.request();this.syncButtons()}else this.enableFollow()}else{this.follow=!this.follow;if(this.follow)this.fit('fleet');else{this.request();this.syncButtons()}}};
   this.zoomSelect=document.createElement('select');this.zoomSelect.id='bmFollowZoom';this.zoomSelect.setAttribute('aria-label','편대 따라가기 확대 방식');this.zoomSelect.innerHTML='<option value="fixed">배율 고정</option><option value="auto">자동 확대·축소</option>';this.zoomSelect.hidden=true;byId('bmFollow').after(this.zoomSelect);this.zoomSelect.onchange=()=>{this.followZoom=this.zoomSelect.value;this.followOutsideSince=0;this.followZoomTick=performance.now();this.syncButtons()};
   this.annotationLayer=document.createElement('div');this.annotationLayer.className='bm-live-labels';this.annotationLayer.hidden=true;this.wrap.append(this.annotationLayer);
  byId('bmGhostBuildings').onclick=()=>{this.ghost=!this.ghost;this.request();this.syncButtons()};
  const manual=(kind='pan')=>{this.manualCamera=true;this.fitRequest=null;this.cameraOnly=false;this.tapCandidate=null;this.gestureMoved=true;
    if(!this.presentation||kind==='pan'){this.viewScope='manual';this.stopFollowing()}
    if(this.presentation&&kind==='wheel'){this.followZoom='fixed';this.followOutsideSince=0}
    this.syncButtons()};
  this.host.addEventListener('pointerdown',event=>{focusPlaybackSurface(event,this.host,this.presentation);this.pointerActive=true;this.beginInteraction();this.dragOrigin=[event.clientX,event.clientY];this.tapCandidate=null;this.gestureMoved=false},{capture:true});
  this.host.addEventListener('pointermove',event=>{
   if(this.dragOrigin&&event.buttons===0){this.dragOrigin=null;this.tapCandidate=null;this.pointerActive=false;void this.finishInteraction()}
   if(this.dragOrigin&&Math.hypot(event.clientX-this.dragOrigin[0],event.clientY-this.dragOrigin[1])>4)manual(this.dragmode==='pan'||(event.buttons&2)!==0||event.shiftKey?'pan':'orbit');
   const node=this.dragOrigin?null:this.nearestProjectedNode(event.clientX,event.clientY),edge=this.dragOrigin||node?null:this.nearestProjectedLink(event.clientX,event.clientY),fleet=this.data?.fleet||[];
   this.host.title=node?`${droneMapLabel(node.id,fleet)} · ${node.role==='relay'?'중계 드론':'수색 드론'} · ${aircraftPaint(this.renderedData?.communication,node.id).description}${finite(node.z)?' · 고도 '+node.z.toFixed(1)+' m':''}`:edge?`${droneMapLabel(edge.node,fleet)} ↔ ${droneMapLabel(edge.peer,fleet)} · ${edge.role==='lostLink'?'직접 링크 단절':'가용 직접 링크'}`:'';
  },{capture:true});
  this.host.addEventListener('pointerup',event=>{if(event.button===0&&this.dragOrigin&&!this.gestureMoved&&Math.hypot(event.clientX-this.dragOrigin[0],event.clientY-this.dragOrigin[1])<=4)this.tapCandidate={x:event.clientX,y:event.clientY,at:performance.now()}},{capture:true});
  this.host.addEventListener('click',event=>{
   const tap=this.tapCandidate;this.tapCandidate=null;
   if(!tap||performance.now()-tap.at>400||this.nativePickAt>=tap.at||Math.hypot(event.clientX-tap.x,event.clientY-tap.y)>4)return;
   const node=this.nearestProjectedNode(event.clientX,event.clientY),edge=node?null:this.nearestProjectedLink(event.clientX,event.clientY);if(!node&&!edge)return;
   this.fallbackPickAt=performance.now();this.onSelect(node?{node:node.id}:{node:edge.node,peer:edge.peer});
  });
  this.host.addEventListener('pointerleave',()=>{this.host.title=''});
  this.host.addEventListener('wheel',()=>{this.beginInteraction();manual('wheel');clearTimeout(this.wheelTimer);this.wheelTimer=setTimeout(()=>{this.wheelTimer=null;void this.finishInteraction()},140)},{passive:true,capture:true});
  window.addEventListener('pointerup',()=>{this.dragOrigin=null;this.pointerActive=false;void this.finishInteraction()});
  const cancelGesture=()=>{this.dragOrigin=null;this.tapCandidate=null;this.pointerActive=false;clearTimeout(this.wheelTimer);this.wheelTimer=null;void this.finishInteraction()};this.host.addEventListener('pointercancel',cancelGesture);window.addEventListener('blur',cancelGesture);
  new ResizeObserver(()=>this.draw()).observe(this.wrap);
   window.__UAV_MAP_TEST__=window.__BUNDANG_MAP_TEST__={snapshot:()=>({active:this.active,ready:this.ready&&!this.busy&&(!this.gestureHeld||this.presentation)&&this.plottedKey===this.visualKey&&this.renderedRunId===this.data?.runId,renderedTime:this.renderedTime,pendingTime:this.data?.time,gestureHeld:this.gestureHeld,runId:this.data?.runId,renderedRunId:this.renderedRunId,region:this.data?.region,sceneRegion:this.spec?.visual_metadata?.region,sceneChunk:this.visualConfig?.map_chunk,sceneSha256:this.visualConfig?.scene_sha256,viewMode:this.viewMode,frameMode:this.viewScope,follow:this.follow,presentation:this.presentation,followZoom:this.followZoom,followInitialFits:this.followInitialFits,presentationFrames:this.presentationFrames,focusScale:this.focusScale,fitResult:this.fitResult||null,dragmode:this.dragmode,camera:clone(this.host.layout?.scene?.camera||this.camera),renderDomain:clone(this.renderDomain),domainInsideIds:[...this.domainInsideIds],projectedPoints:this.projectedPoints,viewport:[this.host.clientWidth,this.host.clientHeight],error:this.error}),plot:()=>this.host};
  this.visibility(false);
 }
 visibility(on){
  this.active=on;this.host.hidden=!on;this.toolbar.hidden=!on;this.status.hidden=!on;
   this.canvas.style.display=on?'none':'';this.wrap.classList.toggle('bundang-map-wrap',on);document.body.classList.toggle('bundang-visual-active',on);this.annotationLayer.hidden=!on||!this.presentation;
  for(const id of ['mapScale','mapCompass']){const el=document.getElementById(id);if(el)el.style.display=on?'none':''}
  if(!on){this.errorHost.hidden=true;this.recovery.hidden=true;this.interactionEpoch++;this.gestureHeld=false;this.pointerActive=false;clearTimeout(this.wheelTimer);this.wheelTimer=null;this.syncInteraction()}
 }
 clearPlot(){if(this.host.data)window.Plotly.purge(this.host);this.ready=false;this.staticCount=0;this.plottedKey=null;this.needsSceneReset=false}
 invalidateScene(){
  // Do not purge concurrently with a Plotly operation. The serialized render
  // queue disposes the old graph before it creates the next region's figure.
  this.epoch++;this.needsSceneReset=true;this.ready=false;this.spec=null;this.renderDomain=null;this.fullMapFocus=null;this.previousGhost=undefined;
  this.renderedTime=null;this.renderedRunId=null;this.plottedKey=null;this.projectedPoints=null;this.domainInsideIds=[];this.host.style.visibility='hidden';
  this.status.querySelector('.bm-direct').textContent='';this.status.querySelector('.bm-detour').hidden=true;this.recovery.hidden=true;this.error=null;this.errorHost.hidden=true;
  selectedReadout(document.getElementById('bmSelectedStatus'),null,null);
  this.interactionEpoch++;this.gestureHeld=false;this.pointerActive=false;this.dragOrigin=null;this.tapCandidate=null;this.fitInterrupted=false;clearTimeout(this.wheelTimer);this.wheelTimer=null;this.syncInteraction();
 }
 beginInteraction(){
  if(!this.active||!this.ready)return;
  this.interactionEpoch++;this.gestureHeld=true;this.syncInteraction();
 }
 syncInteraction(){
   this.wrap.classList.toggle('bm-gesture-held',this.gestureHeld&&!this.presentation);
   const note=this.status.querySelector('.bm-gesture-note');note.hidden=!this.gestureHeld||this.presentation;
  if(this.gestureHeld){const seconds=Math.max(0,this.renderedTime??0),time=String(Math.floor(seconds/60)).padStart(2,'0')+':'+(seconds%60).toFixed(1).padStart(4,'0');note.textContent='화면 기준 '+time+' · 시점 조정 중 · 놓으면 현재 시각 반영'}
 }
 async finishInteraction(){
  if(!this.gestureHeld||this.pointerActive||this.wheelTimer)return;
  const intent=this.interactionEpoch,frame=()=>new Promise(resolve=>requestAnimationFrame(resolve));
  // Let Plotly finish the native pointer-up camera commit. Playback clock keeps
  // running, but no plot mutation competes with a held pointer or wheel gesture.
  await frame();await frame();
   while(!this.presentation&&this.busy&&this.active&&intent===this.interactionEpoch)await frame();
  if(!this.active||!this.gestureHeld||intent!==this.interactionEpoch||this.pointerActive||this.wheelTimer)return;
   if(this.manualCamera){const camera=this.host._fullLayout?.scene?._scene?.getCamera?.()||this.host.layout?.scene?.camera;if(camera)this.camera=clone(camera);this.cameraOnly=!this.presentation}
  else if(this.fitInterrupted)this.fitRequest=this.viewScope==='all'?'all':'fleet';
   this.fitInterrupted=false;this.gestureHeld=false;this.followTick=performance.now();this.syncInteraction();this.refreshProjection();this.request();
 }
 stopFollowing(){if(this.follow){this.follow=false;this.syncButtons()}}
 syncHelp(){if(this.active)document.getElementById('mapHelp').textContent=(this.dragmode==='pan'?'드래그로 이동':'드래그로 회전')+' · 휠로 확대 · 기체를 눌러 선택'}
 syncButtons(){
   for(const [id,on] of [['bmPan',this.dragmode==='pan'],['bmOrbit',this.dragmode==='orbit'],['bmFollow',this.follow],['bmGhostBuildings',this.ghost],['bmFitAll',this.viewScope==='all'],['bmFitFleet',this.viewScope==='fleet']])this.toolbar.querySelector('#'+id)?.setAttribute('aria-pressed',String(on));
   this.zoomSelect.hidden=!this.presentation;this.zoomSelect.value=this.followZoom;this.zoomSelect.disabled=!this.follow;
  const label=this.visualConfig?.label||this.data?.scene?.name||this.data?.region||'지도';
  this.status.querySelector('.bm-engine').textContent=label+' · '+(this.plottedKey!==this.visualKey||this.renderedRunId!==this.data?.runId?'지도 준비 중':this.follow?(this.presentation?'편대 따라가기':'편대 따라보기'):this.viewScope==='all'?'전체 지도':this.viewScope==='fleet'?'현재 편대 보기':'수동 시점');
  for(const el of document.querySelectorAll('[data-map-view]')){const on=el.dataset.mapView===this.viewMode;el.classList.toggle('active',on);el.setAttribute('aria-pressed',String(on))}
  this.syncHelp();
 }
  enableFollow(){
   this.follow=true;this.followTick=0;this.followOutsideSince=0;this.followZoomTick=0;
   this.camera=clone(this.viewMode==='2d'?TOP:CAMERA);this.followInitialFits++;this.fit('fleet');
  }
  setPresentation(enabled){
   this.presentation=enabled;this.wrap.classList.toggle('bm-presentation',enabled);
   const followButton=this.toolbar.querySelector('#bmFollow');followButton.textContent=enabled?'편대 따라가기':'편대 따라보기';followButton.title=enabled?'현재 편대가 보이도록 한 번 맞춘 뒤 중심을 따라갑니다. 휠·회전은 유지하며 이동 드래그는 수동 시점으로 전환합니다.':'재생 중 움직이는 편대를 계속 따라봅니다.';
   if(enabled)this.legacyFleetFit.remove();else if(!this.legacyFleetFit.isConnected)this.toolbar.querySelector('.bm-tools-group').prepend(this.legacyFleetFit);
   this.zoomSelect.hidden=!enabled;this.annotationLayer.hidden=!enabled||!this.active;this.annotationLayer.replaceChildren();
   this.followZoom='fixed';this.followTick=0;this.followOutsideSince=0;this.followZoomTick=0;this.interactionLayoutPending=true;
  }
 setViewMode(mode){const which=this.follow||this.viewScope==='fleet'?'fleet':'all';this.viewMode=mode;this.camera=clone(mode==='2d'?TOP:CAMERA);this.legacy.setViewMode(mode);this.fit(which)}
 reset(){if(!this.active){this.legacy.reset();this.fitRequest='all';return}this.follow=false;this.camera=clone(this.viewMode==='2d'?TOP:CAMERA);this.fit('all')}
 fit(which){if(this.presentation){this.interactionEpoch++;this.fitInterrupted=false}if(which==='all')this.follow=false;this.viewScope=which;this.manualCamera=false;this.fitRequest=which;this.focusScale=1;this.camera=recenteredMainCamera(this.camera,this.viewMode);this.request();this.syncButtons()}
 update(data){
  data.communication=data.run?commAt(data.run,data.frame,data.time,data.selectedNode,data.selectedPeer):null;
  const config=visualMapConfig(data.region),enabled=!!config?.map_chunk&&!!window.Plotly;
  const key=enabled?data.region+':'+config.map_chunk+':'+(config.scene_sha256||''):null,sceneChanged=key!==this.visualKey;
  const changed=data.runId!==this.data?.runId;
   if(this.presentation!==presentationRegion(data))this.setPresentation(presentationRegion(data));
   this.data=data;this.visualConfig=config;
  if(sceneChanged){this.visualKey=key;this.invalidateScene()}
  if(enabled!==this.active)this.visibility(enabled);
  if(!enabled){if(!this.busy&&this.needsSceneReset)this.clearPlot();this.legacy.update(data);selectedReadout(document.getElementById('bmSelectedStatus'),data.communication,data.selectedNode);return}
  this.host.setAttribute('aria-label',(config.label||data.scene?.name||data.region)+' 건물과 드론의 3D 지도');
  if(changed||sceneChanged){this.follow=false;this.viewScope='all';this.manualCamera=false;this.fitRequest='all';this.camera=recenteredMainCamera(this.camera,this.viewMode);this.renderedTime=null;this.renderedRunId=null;this.focusScale=1;this.host.style.visibility='hidden'}
  this.request();this.syncButtons();
 }
 request(){this.dirty=true;void this.render()}
 draw(){
  if(!this.active){this.legacy.draw();return}
  // Size and data updates share the render queue; there is no concurrent
  // Plotly.Plots.resize operation during fullscreen or responsive changes.
  this.resizePending=true;
  this.request();
 }
 framing(data,which){
  const domain=this.renderDomain;
  return {'scene.xaxis.range':[...domain.x],'scene.yaxis.range':[...domain.y],'scene.zaxis.range':[...domain.z],
   'scene.aspectmode':'manual','scene.aspectratio':metricMainAspect(domain,this.focusScale)};
 }
  trackingLayout(nodes){
   const now=performance.now(),dt=this.followTick?Math.min(.2,(now-this.followTick)/1000):0;this.followTick=now;
   if(!this.follow||this.gestureHeld||!nodes.length||!dt)return null;
   const points=projectedMainFleet(this.host,nodes),params=this.host._fullLayout?.scene?._scene?.glplot?.cameraParams;
   if(!points?.length||!params)return null;
   const width=this.host.clientWidth,height=this.host.clientHeight;
   const x=points.reduce((s,p)=>s+p.x,0)/points.length,y=points.reduce((s,p)=>s+p.y,0)/points.length;
   const ox=x-width/2,oy=y-height/2,deadX=Math.max(20,width*.07),deadY=Math.max(18,height*.07);
   const extra=(value,dead)=>Math.abs(value)>dead?value-Math.sign(value)*dead:0;
   const alpha=1-Math.exp(-dt/.32),dx=(extra(ox,deadX)*2/width)/params.projection[0]*alpha,dy=(-extra(oy,deadY)*2/height)/params.projection[5]*alpha;
   const camera=clone(this.host._fullLayout?.scene?._scene?.getCamera?.()||this.camera);camera.center=camera.center||{x:0,y:0,z:0};
   const axes=['x','y','z'],shift=axes.map((axis,i)=>params.view[i*4]*dx+params.view[i*4+1]*dy);
   let scale=1;
   // Optional zoom changes only after a persistent framing error. The default
   // keeps the exact aspect scale and the user's eye/centre offset unchanged.
   if(this.followZoom==='auto'){
    const footprint=Math.max((Math.max(...points.map(p=>p.x))-Math.min(...points.map(p=>p.x)))/Math.max(120,width-110),(Math.max(...points.map(p=>p.y))-Math.min(...points.map(p=>p.y)))/Math.max(150,height-200));
    const outside=footprint>.78||footprint<.22;
    if(!outside)this.followOutsideSince=0;
    else if(!this.followOutsideSince)this.followOutsideSince=now;
    if(outside&&now-this.followOutsideSince>=1200&&now-this.followZoomTick>=2400){
     const desired=Math.max(.86,Math.min(1.10,.5/Math.max(.01,footprint)));
     scale=1+(desired-1)*Math.min(1,dt/.12);this.followZoomTick=now;this.followOutsideSince=now;
    }
   }
   if(Math.abs(dx)<1e-5&&Math.abs(dy)<1e-5&&scale===1)return null;
   for(let i=0;i<3;i++){const axis=axes[i],offset=camera.eye[axis]-camera.center[axis],center=(camera.center[axis]+shift[i])*scale;camera.center[axis]=center;camera.eye[axis]=center+offset}
   const layout={'scene.camera':camera};this.camera=camera;
   if(scale!==1){this.focusScale*=scale;const ratio=this.host.layout.scene.aspectratio;layout['scene.aspectratio']={x:ratio.x*scale,y:ratio.y*scale,z:ratio.z*scale}}
   return layout;
  }
  refreshLiveLabels(nodes=this.currentPlottedNodes(),data=this.renderedData||this.data){
   if(!this.presentation||!this.active||!data)return;
   const selected=new Set([data.selectedNode,data.selectedPeer]),points=projectedMainFleet(this.host,nodes)||[];
   const labels=points.filter(p=>selected.has(p.id)&&p.depth>=-1&&p.depth<=1&&p.x>0&&p.x<this.host.clientWidth&&p.y>0&&p.y<this.host.clientHeight);
   this.annotationLayer.replaceChildren(...labels.map(point=>{
    const label=document.createElement('span'),primary=point.id===data.selectedNode;label.className='bm-live-label';label.textContent=droneMapLabel(point.id,data.fleet||nodes);
    label.style.left=point.x+'px';label.style.top=point.y+'px';label.style.setProperty('--drone-color',aircraftPaint(data.communication,point.id).color);label.dataset.primary=String(primary);return label;
   }));
  }
 async render(){
   if(this.busy||(this.gestureHeld&&!this.presentation)||!this.active||!this.data?.scene||!this.wrap.clientWidth||!this.wrap.clientHeight)return;
  this.busy=true;const epoch=this.epoch,key=this.visualKey,config=this.visualConfig,current=()=>this.active&&epoch===this.epoch&&key===this.visualKey;
  try{
   if(this.needsSceneReset)this.clearPlot();
   if(!this.spec){const spec=await globalThis.__DASHBOARD_READ_CHUNK__(config.map_chunk);if(!current())return;this.spec=spec}
   if(!current())return;
   if(!this.renderDomain){const shared=config.flight_domain;this.renderDomain=computeMainRenderDomain(this.data.scene,this.data.positions,this.spec,shared);this.fullMapFocus=computeMainRenderDomain(this.data.scene,this.data.positions,this.spec,shared,{includeReference:false,pad:false})}
   if(!this.ready){
    const spec=clone(this.spec),staticLayers=spec.data.filter(t=>t.meta?.visual_static||['buildings','buildingOutlines'].includes(t.meta?.role));
    const layout={...spec.layout,autosize:false,height:this.wrap.clientHeight,width:this.wrap.clientWidth,margin:{l:0,r:0,t:0,b:0},showlegend:false,paper_bgcolor:'#0c1825',hoverlabel:{bgcolor:'#102a3c',bordercolor:'#4b718a',font:{color:'#f4fbff',size:12}}};
    layout.scene={...layout.scene,camera:clone(this.camera),dragmode:this.dragmode,uirevision:'uav-map:'+key};
    for(const axis of ['xaxis','yaxis','zaxis'])layout.scene[axis]={...layout.scene[axis],range:[...this.renderDomain[axis[0]]],autorange:false,showbackground:false,showgrid:false,showticklabels:false,showline:false,zeroline:false,title:{text:''}};
    layout.scene.aspectmode='manual';layout.scene.aspectratio=metricMainAspect(this.renderDomain,this.focusScale);
    await window.Plotly.newPlot(this.host,[...staticLayers,...dynamics()],layout,{responsive:false,displayModeBar:false,displaylogo:false,scrollZoom:true});
    if(!current()){this.clearPlot();return}
    this.ready=true;this.staticCount=staticLayers.length;
    this.host.on('plotly_click',ev=>{
     if(this.fallbackPickAt&&performance.now()-this.fallbackPickAt<150)return;
     const point=ev.points?.[0],id=point?.customdata,role=point?.data?.meta?.role,known=new Set((this.data?.position?.uavs||[]).map(n=>n.id));
     const selection=typeof id==='string'&&known.has(id)?{node:id}:['availableLinks','selectedLink','lostLink'].includes(role)&&Array.isArray(id)&&id.length===2&&id.every(value=>known.has(value))?{node:id[0],peer:id[1]}:null;
     if(selection){this.nativePickAt=performance.now();this.onSelect(selection)}
    });
    this.host.on('plotly_relayout',ev=>{
     if(!Object.keys(ev).some(key=>key.startsWith('scene.camera')||this.presentation&&key.startsWith('scene.aspectratio')))return;
     if(this.presentation){
      if(this.gestureHeld||!this.cameraWrite){this.camera=clone(this.host._fullLayout?.scene?._scene?.getCamera?.()||ev['scene.camera']||this.host.layout.scene.camera);const aspect=this.host.layout.scene.aspectratio;if(this.renderDomain&&aspect?.x)this.focusScale=aspect.x/metricMainAspect(this.renderDomain,1).x;this.refreshProjection();this.refreshLiveLabels()}
     }else if(!this.cameraWrite&&this.manualCamera){this.camera=clone(ev['scene.camera']||this.host.layout.scene.camera);this.refreshProjection()}
    });
   }
   while(this.dirty&&(!this.gestureHeld||this.presentation)&&this.active&&epoch===this.epoch){
    this.dirty=false;const data=this.data,intent=this.interactionEpoch,{traces,annotations,pair,nodes}=mainVisualLayers(data);
    const update={x:[],y:[],z:[],customdata:[]},indices=traces.map((t,i)=>this.staticCount+i);
    for(const trace of traces)for(const key of Object.keys(update))update[key].push(trace[key]);
    update.text=traces.map(t=>t.text);
    update.meta=traces.map(t=>t.meta);
    update.hovertext=traces.map(t=>t.hovertext);
    update['marker.color']=traces.map(t=>t.marker?.color);
    update['marker.symbol']=traces.map(t=>t.marker?.symbol);
    const layout=this.presentation?{}:{'scene.annotations':annotations,'scene.dragmode':this.dragmode};
    const fitKind=this.fitRequest||(this.follow?'fleet':this.viewScope==='all'?'all':'fleet'),sizeChanged=this.resizePending||this.host.layout.width!==this.wrap.clientWidth||this.host.layout.height!==this.wrap.clientHeight,needsFit=!!(!this.gestureHeld&&(this.fitRequest||(!this.presentation&&this.follow)||(sizeChanged&&this.viewScope==='all'&&!this.manualCamera)));
    if(!this.gestureHeld&&sizeChanged){layout.width=this.wrap.clientWidth;layout.height=this.wrap.clientHeight;layout['scene.camera']=clone(this.camera)}
    if(needsFit){Object.assign(layout,this.framing(data,fitKind));layout['scene.camera']=clone(this.camera)}
    if(!this.gestureHeld&&this.cameraOnly)layout['scene.camera']=clone(this.camera);
    if(this.presentation){
     if(!this.gestureHeld&&(this.interactionLayoutPending||this.host.layout.scene.dragmode!==this.dragmode))layout['scene.dragmode']=this.dragmode;
     // Position/RF traces keep advancing even while Plotly's native camera
     // gesture is held. Do not send camera, axes or annotations with this call.
     if(this.gestureHeld){const scene=this.host._fullLayout?.scene?._scene;scene?.saveLayout?.(this.host.layout);if(scene?.getCamera)this.camera=clone(scene.getCamera())}
     await window.Plotly.restyle(this.host,update,indices);
     if(!current())return;
     if(!this.gestureHeld&&intent===this.interactionEpoch){
      if(!needsFit)Object.assign(layout,this.trackingLayout(nodes)||{});
      if(Object.keys(layout).length){this.cameraWrite++;try{await window.Plotly.relayout(this.host,layout);if(current()&&needsFit)await this.ensureProjectedFit(fitKind==='all'?mainDomainCorners(this.fullMapFocus):nodes,fitKind,intent,epoch)}finally{this.cameraWrite--}}
      if(intent===this.interactionEpoch&&!this.gestureHeld){if(sizeChanged)this.resizePending=false;if(needsFit){this.fitRequest=null;this.followTick=performance.now()}this.cameraOnly=false;this.interactionLayoutPending=false}
     }
    }else{
     this.cameraWrite++;
     try{await window.Plotly.update(this.host,update,layout,indices);if(current()&&needsFit)await this.ensureProjectedFit(fitKind==='all'?mainDomainCorners(this.fullMapFocus):nodes,fitKind,intent,epoch)}finally{this.cameraWrite--}
     if(sizeChanged)this.resizePending=false;if(needsFit)this.fitRequest=null;this.cameraOnly=false;
    }
    if(!current())return;
    this.renderedTime=data.time;this.renderedRunId=data.runId;this.host.dataset.renderedTime=String(data.time);this.host.dataset.runId=data.runId||'';this.syncInteraction();
    const buildings=this.host.data.map((t,i)=>t.meta?.role==='buildings'?i:-1).filter(i=>i>=0);
    if(!this.gestureHeld&&buildings.length&&this.previousGhost!==this.ghost){const ghost=this.ghost;await window.Plotly.restyle(this.host,{opacity:ghost?.24:.84},buildings);if(current())this.previousGhost=ghost}
    if(!current())return;
    this.plottedKey=key;this.host.style.visibility='';this.syncButtons();
    const a=droneMapLabel(data.selectedNode,data.fleet||nodes),b=droneMapLabel(data.selectedPeer,data.fleet||nodes);
    const down=pair?.available===false,badge=this.status.querySelector('.bm-pair'),detour=badge.querySelector('.bm-detour');
    badge.querySelector('.bm-direct').textContent=a+' ↔ '+b+' · '+(pair?.available===true?'직접 연결':down?'직접 링크 단절':'링크 기록 없음');
    badge.dataset.down=String(down);
    detour.textContent=down?detourText(data.communication?.route,id=>droneMapLabel(id,data.fleet||nodes)):'';
    detour.hidden=!detour.textContent;
    selectedReadout(document.getElementById('bmSelectedStatus'),data.communication,data.selectedNode);
    document.body.classList.toggle('bm-show-actions',!!data.layers?.actions);this.refreshProjection(nodes);this.renderedData=data;
    if(this.presentation){this.presentationFrames++;this.refreshLiveLabels(nodes,data);this.wrap.dispatchEvent(new CustomEvent('uav-map-frame',{bubbles:true,detail:{data,renderer:this,renderedTime:this.renderedTime}}))}
    this.error=null;this.errorHost.hidden=true;
    if(this.presentation)break;
   }
  }catch(error){if(epoch===this.epoch){this.dirty=false;this.error=String(error);this.errorHost.hidden=false;this.errorHost.textContent='3D 지도를 표시할 수 없습니다. '+error.message}}
  finally{this.busy=false;if(!this.active&&this.needsSceneReset)this.clearPlot();if(this.dirty&&this.active&&(!this.gestureHeld||this.presentation))requestAnimationFrame(()=>this.render())}
 }
 refreshProjection(nodes=this.currentPlottedNodes()){
  this.projectedPoints=projectedMainFleet(this.host,nodes);
  const width=this.host.clientWidth,height=this.host.clientHeight;
  const scene=this.host.layout?.scene,domain=scene?{x:scene.xaxis.range,y:scene.yaxis.range,z:scene.zaxis.range}:this.renderDomain;
  this.domainInsideIds=domain?nodes.filter(node=>insideMainRenderDomain(node,domain)).map(node=>node.id):[];
  const inside=new Set(this.domainInsideIds);
  const visible=this.projectedPoints?.filter(p=>inside.has(p.id)&&p.depth>=-1&&p.depth<=1&&p.x>=15&&p.x<=width-15&&p.y>=15&&p.y<=height-15).length;
  this.recovery.hidden=!this.active||!nodes.length||visible!==0;
 }
 currentPlottedNodes(){
  const trace=this.host.data?.find(t=>t.meta?.role==='drones'),fleet=new Map((this.data?.fleet||[]).map(n=>[n.id,n]));
  return (trace?.customdata||[]).map((id,i)=>({...fleet.get(id),id,x:trace.x[i],y:trace.y[i],z:trace.z[i]})).filter(n=>finite(n.x)&&finite(n.y));
 }
 nearestProjectedNode(clientX,clientY){
  if(!this.active||!this.ready)return null;
  // Playback may have queued a newer frame. Hit-test the positions actually
  // plotted on screen, not the newest pending data packet.
  const nodes=this.currentPlottedNodes();
  const projected=projectedMainFleet(this.host,nodes),rect=this.host.getBoundingClientRect();
  let selected=null,distance=14;
  for(const point of projected||[]){if(point.depth< -1||point.depth>1||point.x<0||point.x>rect.width||point.y<0||point.y>rect.height)continue;const d=Math.hypot(clientX-rect.left-point.x,clientY-rect.top-point.y);if(d<=distance){selected=point.id;distance=d}}
  return nodes.find(node=>node.id===selected)||null;
 }
 nearestProjectedLink(clientX,clientY){
  if(!this.active||!this.ready)return null;
  const rect=this.host.getBoundingClientRect(),x=clientX-rect.left,y=clientY-rect.top;
  const known=new Set(this.host.data?.find(t=>t.meta?.role==='drones')?.customdata||[]);
  let result=null,closest=5;
  for(const trace of this.host.data||[]){
   if(!['availableLinks','selectedLink','lostLink'].includes(trace.meta?.role)||trace.visible===false||trace.visible==='legendonly')continue;
   const points=[],segments=[];
   for(let i=0;i<(trace.x?.length||0)-1;i++){
    const pair=trace.customdata?.[i];
    if(!Array.isArray(pair)||pair.length!==2||!pair.every(id=>known.has(id))||![trace.x[i],trace.y[i],trace.z[i],trace.x[i+1],trace.y[i+1],trace.z[i+1]].every(finite))continue;
    const index=points.length;points.push({x:trace.x[i],y:trace.y[i],z:trace.z[i]},{x:trace.x[i+1],y:trace.y[i+1],z:trace.z[i+1]});segments.push({index,pair});
   }
   const projected=projectedMainFleet(this.host,points);if(!projected)continue;
   for(const segment of segments){
    const a=projected[segment.index],b=projected[segment.index+1],dx=b.x-a.x,dy=b.y-a.y,length2=dx*dx+dy*dy;if(length2<1)continue;
    const t=Math.max(0,Math.min(1,((x-a.x)*dx+(y-a.y)*dy)/length2)),distance=Math.hypot(x-a.x-t*dx,y-a.y-t*dy);
    if(distance<=closest){closest=distance;result={node:segment.pair[0],peer:segment.pair[1],role:trace.meta.role}}
   }
  }
  return result;
 }
 async ensureProjectedFit(nodes,kind,intent=this.interactionEpoch,sceneEpoch=this.epoch){
  // Fit the actual screen footprint. Uniform aspect scaling enlarges the
  // view without changing metres, height ratios or clipping the source ranges.
   // Plotly's native camera tick renders now - 2*delay (normally 32 ms).
   // A relayout promise or a single RAF can still expose the previous matrix.
   // Settling each requested view prevents stale offsets being multiplied by
   // the next zoom step. This affects only explicit Bundang framing, not drag.
   const settle=async()=>{
    if(this.presentation){
     const delay=Math.max(0,Number(this.host._fullLayout?.scene?._scene?.camera?.delay)||0);
     await new Promise(resolve=>setTimeout(resolve,2*delay+8));
    }else await new Promise(resolve=>requestAnimationFrame(resolve));
    if(sceneEpoch!==this.epoch)return false;
    if(this.gestureHeld||intent!==this.interactionEpoch){this.fitInterrupted=true;return false}
    if(this.manualCamera)return false;
    if(this.presentation)this.host._fullLayout?.scene?._scene?.glplot?.redraw?.();
    return true;
   };
   const verify=attempts=>{
    const points=projectedMainFleet(this.host,nodes)||[],width=this.host.clientWidth,height=this.host.clientHeight;
    const outside=points.filter(p=>!Number.isFinite(p.x)||!Number.isFinite(p.y)||p.x<10||p.x>width-10||p.y<10||p.y>height-10||p.depth< -1||p.depth>1).length;
    this.fitResult={kind,attempts,points:points.length,outside,passed:points.length===nodes.length&&outside===0,cameraDelayMs:Number(this.host._fullLayout?.scene?._scene?.camera?.delay)||0};
    if(this.presentation&&!this.fitResult.passed)throw new Error('편대 시야 맞춤을 완료하지 못했습니다. 전체 지도 또는 편대 따라가기를 다시 선택해 주세요.');
   };
   let attempts=0;
   for(let attempt=0;attempt<(this.presentation?6:4);attempt++){
    if(!await settle())return;attempts=attempt+1;
   const projected=projectedMainFleet(this.host,nodes);if(!projected?.length)return;
   const width=this.host.clientWidth,height=this.host.clientHeight;
   const usableWidth=Math.max(120,width-110),usableHeight=Math.max(150,height-200);
   const xmin=Math.min(...projected.map(p=>p.x)),xmax=Math.max(...projected.map(p=>p.x)),ymin=Math.min(...projected.map(p=>p.y)),ymax=Math.max(...projected.map(p=>p.y));
   const params=this.host._fullLayout?.scene?._scene?.glplot?.cameraParams;if(!params)return;
   let scale=1;
   const footprint=Math.max((xmax-xmin)/usableWidth,(ymax-ymin)/usableHeight),target=kind==='all'?.9:.48,min=kind==='all'?.84:.43,max=kind==='all'?.94:.54;
   if(footprint>1e-5&&(footprint<min||footprint>max))scale=Math.max(.4,Math.min(3,target/footprint));
   const dx=(((xmin+xmax)/2-width/2)*2/width)/params.projection[0];
   const dy=((height/2-(ymin+ymax)/2)*2/height)/params.projection[5];
   const shift=[0,1,2].map(i=>params.view[i*4]*dx+params.view[i*4+1]*dy);
   if(Math.abs(scale-1)<.001&&Math.abs(dx)<.008&&Math.abs(dy)<.008){if(this.presentation)verify(attempts);return}
   const camera=clone(this.camera),axes=['x','y','z'];camera.center=camera.center||{x:0,y:0,z:0};
   for(let i=0;i<3;i++){const axis=axes[i],offset=camera.eye[axis]-camera.center[axis],center=(camera.center[axis]+shift[i])*scale;camera.center[axis]=center;camera.eye[axis]=center+offset}
   const aspect=this.host.layout.scene.aspectratio,update={'scene.camera':camera,'scene.aspectratio':{x:aspect.x*scale,y:aspect.y*scale,z:aspect.z*scale}};
   this.camera=camera;this.focusScale*=scale;
   await window.Plotly.relayout(this.host,update);
  }
   if(this.presentation&&await settle())verify(attempts);
 }
}
export const BundangMapRenderer=UavMapRenderer;
