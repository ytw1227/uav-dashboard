/** Orthographic XYZ view of saved metric coordinates; never alters simulation data. */
const finite = x => typeof x === 'number' && Number.isFinite(x);
export const DEFAULT_CAMERA = Object.freeze({yaw: -.55, pitch: Math.PI / 3});
export const MIN_PITCH = .22;
export const MAX_PITCH = Math.PI / 2 - .025;
export const clampPitch = pitch => Math.max(MIN_PITCH, Math.min(MAX_PITCH, pitch));
export function geometryPolygons(g) {
 if (!g) return [];
 if (g.type === 'Feature') return geometryPolygons(g.geometry);
 if (g.type === 'Polygon') return [g.coordinates];
 if (g.type === 'MultiPolygon') return g.coordinates;
 if (Array.isArray(g) && Array.isArray(g[0])) return typeof g[0][0] === 'number' ? [[g]] : [g];
 return [];
}
export function buildingHeight(building, ground = 0) {
 const height = finite(building.height_m) && building.height_m >= 0 ? building.height_m : null;
 const estimated = building.imputed === true || building.height_status === 'estimated' || building.height_source === 'imputed';
 return {ground, height, top: ground + (height ?? 0), status: height === null ? 'unknown' : estimated ? 'estimated' : 'recorded'};
}
/** Camera basis is orthonormal: horizontal east/north rotation, then pitch. Depth increases toward viewer. */
export function createProjection({bounds, ground = 0, maxZ = ground, width, height, zoom = 1, pan = [0, 0], yaw = DEFAULT_CAMERA.yaw, pitch = DEFAULT_CAMERA.pitch}) {
 if (!Array.isArray(bounds) || bounds.length !== 4 || !bounds.every(finite) || bounds[2] <= bounds[0] || bounds[3] <= bounds[1] || !finite(width) || !finite(height) || width <= 0 || height <= 0) return null;
 yaw = finite(yaw) ? yaw : DEFAULT_CAMERA.yaw; pitch = clampPitch(finite(pitch) ? pitch : DEFAULT_CAMERA.pitch);
 ground = finite(ground) ? ground : 0; maxZ = finite(maxZ) ? Math.max(ground, maxZ) : ground;
 const [xmin,ymin,xmax,ymax] = bounds, cx = (xmin+xmax)/2, cy = (ymin+ymax)/2;
 const co=Math.cos(yaw),si=Math.sin(yaw),sp=Math.sin(pitch),cp=Math.cos(pitch);
 const view = (x,y,z) => {const dx=x-cx,dy=y-cy,u=co*dx-si*dy,v=si*dx+co*dy,h=z-ground;return [u,-v*sp-h*cp,-v*cp+h*sp]};
 const corners=[];for(const x of [xmin,xmax])for(const y of [ymin,ymax])for(const z of [ground,maxZ])corners.push(view(x,y,z));
 const ys=corners.map(p=>p[1]),xs=corners.map(p=>p[0]);
 const xspan=Math.max(...xs)-Math.min(...xs),yspan=Math.max(...ys)-Math.min(...ys),middleY=(Math.max(...ys)+Math.min(...ys))/2;
 const baseScale=Math.max(.00001,Math.min(Math.max(1,width-90)/xspan,Math.max(1,height-85)/Math.max(1,yspan)));
 const scale=baseScale*(finite(zoom)?Math.max(.01,zoom):1),px=finite(pan[0])?pan[0]:0,py=finite(pan[1])?pan[1]:0;
 const project = (x,y,z=ground) => {if(!finite(x)||!finite(y)||!finite(z))return null;const p=view(x,y,z);return {x:width/2+p[0]*scale+px,y:height/2+(p[1]-middleY)*scale+py,depth:p[2]}};
 return {project,scale,baseScale,ground,yaw,pitch};
}
export function metricScaleBar(scale,targetPixels=100){
 if(!finite(scale)||scale<=0)return null;const target=targetPixels/scale,power=10**Math.floor(Math.log10(target)),fraction=target/power,metres=(fraction>=5?5:fraction>=2?2:1)*power;return {metres,pixels:metres*scale};
}
const sceneCache = new WeakMap();
function sceneGeometry(scene) {
 if(sceneCache.has(scene))return sceneCache.get(scene);
 const groundRecorded=finite(scene.coordinate_system?.ground_z_m),ground=groundRecorded?scene.coordinate_system.ground_z_m:0;
 const buildings=[];let maxZ=ground;const counts={recorded:0,estimated:0,unknown:0};
 for(const b of scene.buildings||[]){const h=buildingHeight(b,ground);counts[h.status]++;maxZ=Math.max(maxZ,h.top);
  for(const polygon of geometryPolygons(b.geometry||b.geometry_local_m)){
   const rings=polygon.map(r=>(Array.isArray(r)?r:[]).filter(p=>Array.isArray(p)&&finite(p[0])&&finite(p[1]))).filter(r=>r.length>=3);
   if(rings.length)buildings.push({rings,...h});
  }
 }
 const result={ground,groundRecorded,buildings,maxZ,counts};sceneCache.set(scene,result);return result;
}
function path(c,rings){c.beginPath();for(const ring of rings){ring.forEach((p,i)=>i?c.lineTo(p.x,p.y):c.moveTo(p.x,p.y));c.closePath()}}
function polygon(c,rings,fill,stroke,dashed=false,hatch=false,w=0,h=0){
 path(c,rings);c.fillStyle=fill;c.fill('evenodd');c.strokeStyle=stroke;c.lineWidth=.65;c.setLineDash(dashed?[3,3]:[]);c.stroke();c.setLineDash([]);
 if(hatch){c.save();path(c,rings);c.clip('evenodd');c.strokeStyle='#61788d55';c.lineWidth=.65;const ps=rings.flat(),xmin=Math.max(-h,Math.min(...ps.map(p=>p.x))),xmax=Math.min(w+h,Math.max(...ps.map(p=>p.x))),ymin=Math.max(0,Math.min(...ps.map(p=>p.y))),ymax=Math.min(h,Math.max(...ps.map(p=>p.y)));for(let x=xmin-(ymax-ymin);x<xmax;x+=7){c.beginPath();c.moveTo(x,ymin);c.lineTo(x+ymax-ymin,ymax);c.stroke()}c.restore()}
}
function line(c,a,b,color,width=1,dash=false){c.beginPath();c.moveTo(a.x,a.y);c.lineTo(b.x,b.y);c.strokeStyle=color;c.lineWidth=width;c.setLineDash(dash?[4,3]:[]);c.stroke();c.setLineDash([])}
function depthOf(rings){const all=rings.flat();return all.reduce((s,p)=>s+p.depth,0)/all.length}
function pointInside(point,rings){let inside=false;for(const ring of rings){for(let i=0,j=ring.length-1;i<ring.length;j=i++){const a=ring[i],b=ring[j];if(((a.y>point.y)!==(b.y>point.y))&&point.x<(b.x-a.x)*(point.y-a.y)/(b.y-a.y)+a.x)inside=!inside}}return inside}
export function render3D(renderer,{width:w,height:h,positionAt}) {
 const c=renderer.ctx,{scene,position,positions=[],frame,decision,completedDecision,time:t=0,selectedNode,selectedPeer,event,layers={}}=renderer.data;
 const geo=sceneGeometry(scene),nodes=(position?.uavs||[]).filter(n=>finite(n.x)&&finite(n.y));
 // Stable fitting over all saved heights, independent of the selected replay time.
 let maxZ=geo.maxZ;for(const p of positions)for(const n of p.uavs||[])if(finite(n.z))maxZ=Math.max(maxZ,n.z);for(const n of nodes)if(finite(n.z))maxZ=Math.max(maxZ,n.z);
 const camera=createProjection({bounds:scene.bounds,ground:geo.ground,maxZ,width:w,height:h,zoom:renderer.zoom,pan:renderer.pan,...renderer.camera});renderer.projection=camera;if(!camera)return;
 const {project:P,scale}=camera,ground=geo.ground,Z=n=>finite(n.z)?n.z:ground;
 renderer.heightStatus={...geo.counts,ground_recorded:geo.groundRecorded,unrecorded_uav_altitudes:nodes.filter(n=>!finite(n.z)).length};
 const [xmin,ymin,xmax,ymax]=scene.bounds,span=Math.max(xmax-xmin,ymax-ymin),nice=10**Math.floor(Math.log10(span/8)),grid=nice*(span/nice/8>2?2:1);
 const groundRing=[[xmin,ymin],[xmax,ymin],[xmax,ymax],[xmin,ymax]].map(p=>P(...p,ground));polygon(c,[groundRing],'#eaf1f2','#bacbd3');
 for(let x=Math.ceil(xmin/grid)*grid;x<=xmax;x+=grid)line(c,P(x,ymin,ground),P(x,ymax,ground),'#dce6eb',.7);
 for(let y=Math.ceil(ymin/grid)*grid;y<=ymax;y+=grid)line(c,P(xmin,y,ground),P(xmax,y,ground),'#dce6eb',.7);
 for(const zone of scene.no_fly_zones||[])for(const rings of geometryPolygons(zone.geometry||zone.geometry_local_m||zone)){
  const p=rings.map(r=>r.map(x=>P(x[0],x[1],ground)).filter(Boolean)).filter(r=>r.length>=3);if(p.length)polygon(c,p,'#d88e9425','#bd747a88',true);
 }
 const queue=[];let order=0;
 const add=(depth,paint,kind='line',detail=null)=>queue.push({depth,paint,kind,detail,order:order++});
 const addFace=(rings,fill,stroke,hatch=false,dashed=false)=>add(depthOf(rings),()=>polygon(c,rings,fill,stroke,dashed,hatch,w,h),'building',rings);
 for(const b of geo.buildings){
  const bottom=b.rings.map(r=>r.map(p=>P(p[0],p[1],ground))),top=b.rings.map(r=>r.map(p=>P(p[0],p[1],b.top)));
  if(b.height>0)for(let ri=0;ri<b.rings.length;ri++){const ring=b.rings[ri];for(let i=0;i<ring.length;i++){const j=(i+1)%ring.length;if(ring[i][0]===ring[j][0]&&ring[i][1]===ring[j][1])continue;const side=[bottom[ri][i],bottom[ri][j],top[ri][j],top[ri][i]],shade=(ring[j][0]-ring[i][0])*Math.cos(camera.yaw)-(ring[j][1]-ring[i][1])*Math.sin(camera.yaw);addFace([side],shade>0?'#bfcdd7':'#adbeca','#8fa4b3',false,b.status==='estimated')}}
  addFace(top,b.status==='unknown'?'#eef1f4':b.status==='estimated'?'#dce3e9':'#d5e0e7','#a0b2c0',b.status!=='recorded',b.status!=='recorded');
 }
 const nodeMap=new Map(nodes.map(n=>[n.id,n]));
 const segment=(a,b,color,width,dash=false,kind='line')=>{
  if(!a||!b||![...a,...b].every(finite))return;
  const length=Math.hypot(...a.map((v,i)=>v-b[i])),parts=Math.max(1,Math.min(64,Math.ceil(length/Math.max(5,span/48))));
  for(let i=0;i<parts;i++){const q=i/parts,r=(i+1)/parts,aa=P(...a.map((v,k)=>v+(b[k]-v)*q)),bb=P(...a.map((v,k)=>v+(b[k]-v)*r));add((aa.depth+bb.depth)/2,()=>line(c,aa,bb,color,width,dash),kind)}
 };
 // Ground projections represent x/y only; dashed stems expose the recorded altitude.
 for(const n of nodes){const p=P(n.x,n.y,ground);c.beginPath();c.ellipse(p.x,p.y,5,2.5,0,0,Math.PI*2);c.fillStyle='#32546922';c.fill();if(finite(n.z)&&n.z!==ground)segment([n.x,n.y,ground],[n.x,n.y,n.z],'#5e7f9560',.7,true)}
 if(layers.tracks)for(const n of nodes){let prev=null;const color=n.id===selectedNode?'#25637bb0':n.role==='search'?'#0d938d60':'#193b5a45';for(const pos of positions){if(!layers.future&&pos.sample_time_s>t)break;const u=(pos.uavs||[]).find(u=>u.id===n.id);if(!u||!finite(u.x)||!finite(u.y)){prev=null;continue}const current=[u.x,u.y,Z(u)];if(prev)segment(prev,current,color,n.id===selectedNode?1.9:1,!!layers.future,'track');prev=current}if(prev&&!layers.future)segment(prev,[n.x,n.y,Z(n)],color,n.id===selectedNode?1.9:1,false,'track')}
 if(layers.links&&frame)for(const l of frame.links||[]){const a=nodeMap.get(l.source),b=nodeMap.get(l.target);if(!a||!b)continue;const chosen=(l.source===selectedNode&&l.target===selectedPeer)||(l.target===selectedNode&&l.source===selectedPeer),related=event?.related_link_ids?.includes(l.id);if(!l.available&&!chosen&&!related)continue;segment([a.x,a.y,Z(a)],[b.x,b.y,Z(b)],l.available?(chosen?'#087c75':'#5e9fa199'):'#c95b65',chosen?2.5:related?2:1.2,!l.available,'link');const aa=P(a.x,a.y,Z(a)),bb=P(b.x,b.y,Z(b));renderer.linkHit.push({...l,a:[aa.x,aa.y],b:[bb.x,bb.y],depth:(aa.depth+bb.depth)/2})}
 function vector(n,v,color,width,dash){if(!Array.isArray(v)||v.length<2||!v.every(finite)||Math.hypot(...v)<.01)return;const a=[n.x,n.y,Z(n)],b=[n.x+v[0],n.y+v[1],Z(n)+(v[2]??0)],aa=P(...a),bb=P(...b);if(!aa||!bb)return;segment(a,b,color,width,dash,'action');const angle=Math.atan2(bb.y-aa.y,bb.x-aa.x);add(bb.depth,()=>{c.beginPath();c.moveTo(bb.x,bb.y);c.lineTo(bb.x-6*Math.cos(angle-.45),bb.y-6*Math.sin(angle-.45));c.lineTo(bb.x-6*Math.cos(angle+.45),bb.y-6*Math.sin(angle+.45));c.closePath();c.fillStyle=color;c.fill()},'action')}
 if(layers.actions&&decision){const base=positionAt(positions,decision.decision_time_s);for(const a of decision.actions||[]){const n=base.uavs.find(n=>n.id===a.uav_id);if(!n)continue;const width=a.uav_id===selectedNode?2.1:1.3;vector(n,a.requested_correction,'#ca893a',width,true);vector(n,a.applied_correction,'#8964b0',width,false);if(layers.future)vector(n,a.actual_displacement,'#24485f',width,false)}}
 if(layers.actions&&completedDecision&&!layers.future){const base=positionAt(positions,completedDecision.decision_time_s);for(const a of completedDecision.actions||[]){const n=base.uavs.find(n=>n.id===a.uav_id);if(n)vector(n,a.actual_displacement,'#24485f',a.uav_id===selectedNode?2.2:1.4,false)}}
 if(event?.marker){const marker=event.marker,xy=marker.position||marker.xy||(finite(marker.x)?[marker.x,marker.y]:null);if(xy){const p=P(xy[0],xy[1],finite(xy[2])?xy[2]:finite(marker.z)?marker.z:ground);if(p)add(p.depth,()=>{c.beginPath();c.arc(p.x,p.y,19,0,2*Math.PI);c.strokeStyle='#cb5c6188';c.setLineDash([3,3]);c.stroke();c.setLineDash([])},'event')}}
 for(const n of nodes){const p=P(n.x,n.y,Z(n)),selected=n.id===selectedNode,related=event?.related_uav_ids?.includes(n.id);add(p.depth,()=>{
  if(selected||related){c.beginPath();c.arc(p.x,p.y,selected?15:12,0,2*Math.PI);c.fillStyle=selected?'#0d938d1a':'#d9686d14';c.fill();c.strokeStyle=selected?'#0d938d99':'#cf6c7499';c.lineWidth=1;c.stroke()}
  c.beginPath();c.arc(p.x,p.y,5.5,0,2*Math.PI);c.fillStyle=n.role==='search'?'#0d938d':'#294b68';c.fill();c.strokeStyle='#fff';c.lineWidth=2;c.stroke();c.fillStyle='#284458';c.font=`${selected?'bold ':''}10px system-ui`;c.textAlign='center';c.fillText(String(n.id).replace('uav-','U')+(finite(n.z)?'':' · 고도?'),p.x,p.y-12);
 },'node',{id:n.id,...p})}
 queue.sort((a,b)=>a.depth-b.depth||a.order-b.order);
 // Hit targets follow visible depth order. A building painted over a UAV hides its hit target too.
 const visible=[];
 for(const item of queue){item.paint();if(item.kind==='building'){for(let i=visible.length-1;i>=0;i--)if(pointInside(visible[i],item.detail))visible.splice(i,1)}else if(item.kind==='node')visible.push(item.detail)}
 renderer.hit=visible.reverse();renderer.linkHit.sort((a,b)=>b.depth-a.depth);
 renderer.last3DStats={buildings:geo.buildings.length,nodes:nodes.length,commands:queue.length,kinds:queue.reduce((o,i)=>(o[i.kind]=(o[i.kind]||0)+1,o),{})};
 // Camera orientation is in local east/north metres, not a basemap bearing.
 c.save();c.translate(w-42,h-42);const north=P((xmin+xmax)/2,(ymin+ymax)/2+1,ground),origin=P((xmin+xmax)/2,(ymin+ymax)/2,ground),len=Math.hypot(north.x-origin.x,north.y-origin.y)||1;line(c,{x:0,y:0},{x:(north.x-origin.x)/len*20,y:(north.y-origin.y)/len*20},'#426479',1.4);c.fillStyle='#426479';c.font='bold 10px system-ui';c.textAlign='center';c.fillText('N',(north.x-origin.x)/len*29,(north.y-origin.y)/len*29+3);c.restore();
 c.fillStyle='#60798a';c.textAlign='left';c.font='10px system-ui';c.fillText('3D · 높이 1:1 · 점선: 고도 / 해칭: 추정·미기록',14,65);if(!geo.groundRecorded)c.fillText('지면 높이 미기록 · 표시 기준 0 m',14,81);
 const scaleEl=globalThis.document?.getElementById('mapScale');if(scaleEl){const bar=metricScaleBar(scale);scaleEl.style.width=`${bar.pixels}px`;scaleEl.textContent=`${bar.metres.toLocaleString('ko-KR',{maximumFractionDigits:6})} m · 화면 수평`}
}
