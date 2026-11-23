import {intervalAt,positionAt} from './render.js';
import {droneMapLabel} from './map3d.js';
import {commAt} from './comm_state.js';
import {aircraftPaint,selectedReadout} from './map_status.js';
import {projectedMainFleet,computeMainRenderDomain,metricMainAspect,visualMapConfig} from './main_plotly.js';

const ink = {loss:'#b63e46',move:'#7d42a6',recovery:'#258152',trail:'#516b88'};
const pilotInk={loss:'#ff626b',move:'#b68cff',recovery:'#54e6a3',trail:'#8ba8c0'};
const camera = {up:{x:0,y:0,z:1},eye:{x:.055,y:-.22,z:.95},projection:{type:'orthographic'}};
const pilotCamera={up:{x:0,y:0,z:1},eye:{x:.54,y:-.76,z:1.34},projection:{type:'orthographic'}};
const topCamera={up:{x:0,y:1,z:0},eye:{x:0,y:0,z:1.55},projection:{type:'orthographic'}};
export const hasVisualScene=c=>Boolean(c.run?.region&&c.spec?.visual_metadata?.region===c.run.region&&visualMapConfig(c.run.region));
const hasLiveIncident=c=>hasVisualScene(c);
const step = 0.5;
const copy = obj => JSON.parse(JSON.stringify(obj));
const edgeKey = (a,b) => a < b ? a+'|'+b : b+'|'+a;
const mapQueues=new WeakMap();
const liveFrames=new WeakMap();
function mapMutation(map,operation){
    const job=Promise.resolve(mapQueues.get(map)).catch(()=>{}).then(()=>{
        if(!map.isConnected||!map._fullLayout||map.__visualDisposed)return;
        return operation();
    });
    mapQueues.set(map,job.catch(()=>{}));
    return job;
}
export function displayDrone(id,fleet) {
    return droneMapLabel(id,fleet);
}
function pointsAt(c,t) {
    const frame=Math.round((t-c.samples[0]?.time)/step);
    const sample=c.samples[frame];
    if(sample?.time===t)return sample.p;
    // A neighbouring incident can start before the viewport and finish inside it.
    const rows=positionAt(c.run.overview.positions,t).uavs;
    return new Map(rows.map(node=>[node.id,node]));
}
function xyz(tr,p,detail) {
    tr.x.push(p.x);tr.y.push(p.y);tr.z.push(p.z);tr.customdata.push(detail);
}
function segment(tr,a,b,value) {
    if(!a||!b)return;
    xyz(tr,a,value);xyz(tr,b,value);
    tr.x.push(null);tr.y.push(null);tr.z.push(null);tr.customdata.push(null);
}
function frameBounds(c, layout){
    if(hasVisualScene(c)){
        const metadata=c.spec.visual_metadata,source={bounds:metadata.bounds,coordinate_system:metadata.coordinate_system};
        const domain=computeMainRenderDomain(source,c.run.overview.positions,c.spec,visualMapConfig(c.run.region)?.flight_domain);
        const scene=layout.scene;
        for(const [name,key] of [['xaxis','x'],['yaxis','y'],['zaxis','z']]){
            Object.assign(scene[name],{range:domain[key],autorange:false,showbackground:false,showgrid:false,
                showticklabels:false,showline:false,zeroline:false,showspikes:false,title:{text:''}});
        }
        scene.aspectmode='manual';scene.aspectratio=metricMainAspect(domain);
        scene.camera=copy(pilotCamera);scene.dragmode='pan';scene.bgcolor='#0c1825';
        scene.uirevision=c.run.id+'|'+c.event.id;layout.uirevision=scene.uirevision;layout.paper_bgcolor='#0c1825';
        return;
    }
    const xs=[],ys=[],zs=[];
    for(const row of c.samples){
        for(const p of row.p.values()){
            xs.push(p.x);ys.push(p.y);zs.push(p.z);
        }
    }
    if(!xs.length)return;
    const [west,east]=[Math.min(...xs),Math.max(...xs)], [south,north]=[Math.min(...ys),Math.max(...ys)];
    const aspect=Math.max(.8,c.map.clientWidth/Math.max(1,c.map.clientHeight));
    const depth=Math.max(96,north-south+30,(east-west+30)/aspect);
    const width=depth*aspect;
    const mx=(west+east)/2,my=(south+north)/2;
    const scene=layout.scene;
    scene.xaxis.range=[mx-width/2,mx+width/2];
    scene.yaxis.range=[my-depth/2,my+depth/2];
    // Keep the horizontal and vertical scales metric. Only nearby roofs influence framing.
    let ceiling=Math.max(...zs)+12;
    const mesh=c.spec.data.find(tr=>tr.meta?.role==='buildings');
    if(mesh){
        for(let i=0;i<mesh.x.length;i++){
            if(Math.abs(mesh.x[i]-mx)<=width/2 && Math.abs(mesh.y[i]-my)<=depth/2){
                ceiling=Math.max(ceiling,mesh.z[i]+2);
            }
        }
    }
    scene.zaxis.range=[0,ceiling];
    scene.aspectmode='manual';
    const magnification=2.06/depth;
    scene.aspectratio={x:width*magnification,y:depth*magnification,z:ceiling*magnification};
    scene.camera=copy(hasVisualScene(c)?pilotCamera:camera);
    scene.uirevision=c.run.id+'|'+c.event.id;
    layout.uirevision=scene.uirevision;
    for(const axis of [scene.xaxis,scene.yaxis,scene.zaxis]){
        axis.autorange=false;
        axis.backgroundcolor='#f5f7f7';
        axis.gridcolor='#dce3e4';
        axis.showticklabels=false;
        axis.title={text:''};
        axis.showline=false;
        axis.zeroline=false;
    }
    // Keep the ground reference; omit vertical grid walls that obscure small aircraft.
    scene.xaxis.showbackground=false;scene.yaxis.showbackground=false;
    scene.xaxis.showgrid=false;scene.yaxis.showgrid=false;
    scene.zaxis.showgrid=true;scene.zaxis.dtick=50;
    layout.paper_bgcolor='#f7f9f9';
    if(hasVisualScene(c)){
        scene.dragmode='pan';scene.zaxis.showgrid=false;
        scene.bgcolor='#0c1825';layout.paper_bgcolor='#0c1825';
    }
}
function lineLayer(template,role,label,color,width,dash) {
    const tr={...copy(template),name:label,meta:{role},line:{color,width,dash},
        hoverinfo:'skip',connectgaps:false,x:[],y:[],z:[],customdata:[],text:[]};
    return tr;
}
export function incidentFigure(c) {
    const layout=copy(c.spec.layout);
    frameBounds(c,layout);
    const base=c.spec.data;
    const pilot=hasVisualScene(c);
    const colors=pilot?pilotInk:ink;
    c.map.closest('dialog')?.classList.toggle('uav-visual-incident',pilot);
    const layers=base.filter(t=>['buildings','buildingOutlines'].includes(t.meta?.role)||(pilot&&t.meta?.visual_static&&t.meta.role!=='noFlyZones')).map(copy);
    const mesh=layers.find(t=>t.meta?.role==='buildings');
    if(mesh&&!pilot){
        const palette={'#9faeb0':'#aebfc5','#c6cfce':'#e0e8e8','#b0b2a8':'#bdbfb3','#d0d1c6':'#e5e4d9'};
        mesh.facecolor=mesh.facecolor.map(color=>palette[color]||color);
        mesh.lighting={ambient:1,diffuse:.2,specular:.01};
    }
    const lines=base.find(t=>t.meta?.role==='lostLinks');
    layers.push(
        lineLayer(lines,'trackedPath','선택 기체 궤적',colors.trail,3,'solid'),
        lineLayer(lines,'lostLinks','단절 시작 링크',colors.loss,5,'dash'),
        lineLayer(lines,'actualTrack','확인된 이동',colors.move,8,'solid'),
        lineLayer(lines,'recoveryPath','재연결 경로',colors.recovery,7,'solid')
    );
    const drones=copy(base.find(t=>t.meta?.role==='drones'));
    drones.mode='markers';
    drones.marker={size:6.25,symbol:'circle',color:'#9baaba',line:{color:'#f2fbff',width:.85}};
    drones.textfont={family:'Arial, sans-serif',size:13,color:'#182d40',weight:700};
    drones.textposition='top center';
    drones.hovertemplate='<b>%{text}</b> · %{hovertext}<br>동 %{x:.1f} m · 북 %{y:.1f} m · 고도 %{z:.1f} m<extra></extra>';
    layers.push({type:'scatter3d',mode:'markers',name:'선택한 기체',meta:{role:'selectedHalo'},
        x:[],y:[],z:[],customdata:[],text:[],hoverinfo:'skip',
        marker:{symbol:'circle-open',size:12,color:'#ecfbff',line:{color:'#ecfbff',width:1.5}}});
    layers.push(drones);
    if(pilot)installPilotControls(c);
    return {data:layers,layout};
}
function installPilotControls(c){
    const host=c.map.parentElement;
    const toolbar=document.createElement('div');toolbar.className='incident-camera-tools';toolbar.setAttribute('role','group');toolbar.setAttribute('aria-label','사건 지도 시점');
    const values=[['Pan','이동','지도를 잡아 원하는 위치로 이동'],['Orbit','회전','드래그로 입체 시점 회전'],['Top','위에서','북쪽이 위인 수직 시점'],['Oblique','입체','입체 시점으로 돌아가기'],['Ghost','건물 연하게','건물 뒤의 기체를 보기 위한 표시 옵션']];
    for(const [key,label,title] of values){
        const button=document.createElement('button');button.type='button';button.id='incidentCamera'+key;button.textContent=label;button.title=title;
        button.className='incident-camera-button';button.setAttribute('aria-pressed',String(key==='Pan'||key==='Oblique'));
        button.onclick=()=>{
            const plot=c.map;
            if(!plot?._fullLayout)return;
            if(key==='Ghost'){
                const active=button.getAttribute('aria-pressed')!=='true';
                mapMutation(plot,()=>{
                    const mesh=plot.data.findIndex(trace=>trace.meta?.role==='buildings');
                    if(mesh>=0)return window.Plotly.restyle(plot,{'opacity':active?.24:.84},[mesh]);
                });
                button.setAttribute('aria-pressed',String(active));
            }else if(key==='Pan'||key==='Orbit'){
                mapMutation(plot,()=>window.Plotly.relayout(plot,{'scene.dragmode':key==='Pan'?'pan':'orbit'}));
                for(const name of ['Pan','Orbit'])toolbar.querySelector('#incidentCamera'+name)?.setAttribute('aria-pressed',String(name===key));
            }else{
                focusIncidentView(window.Plotly,c,key==='Top'?topCamera:pilotCamera);
                for(const name of ['Top','Oblique'])toolbar.querySelector('#incidentCamera'+name)?.setAttribute('aria-pressed',String(name===key));
            }
        };
        toolbar.append(button);
    }
    host.append(toolbar);
    const clock=document.createElement('div');clock.className='incident-map-clock';clock.innerHTML='<small>비행 기록</small><output></output>';host.append(clock);
    const guide=document.createElement('div');guide.className='incident-height-key';guide.innerHTML='<span><i></i>기록 높이</span><span><i class="estimated"></i>추정 높이</span><small>동·북·높이 동일 축척 · m</small>';
    host.append(guide);
    let pending,priorSize='';
    const resize=()=>{
        clearTimeout(pending);
        pending=setTimeout(async()=>{
            if(!c.map.isConnected||c.map.__visualDisposed){observer.disconnect();return;}
            if(!c.map.closest('dialog')?.open)return;
            await Promise.resolve(c.work).catch(()=>{});
            if(c.busy){resize();return;}
            if(c.map.__visualInteracting){c.map.__visualResizePending=true;return;}
            const size=c.map.clientWidth+'|'+c.map.clientHeight;
            if(size===priorSize||!c.map._fullLayout)return;
            priorSize=size;
            if(size===c.map.__visualFitSize)return;
            await mapMutation(c.map,async()=>{
                if(c.map.__visualInteracting){c.map.__visualResizePending=true;return;}
                await window.Plotly.Plots.resize(c.map);
                if(!c.map.__visualDisposed&&!c.map.__visualManualView)await fitIncidentCamera(window.Plotly,c,c.map.layout.scene.camera);
            });
        },80);
    };
    const observer=new ResizeObserver(resize);observer.observe(host);
    const disposeInteraction=installPilotInteraction(c,clock,resize);
    const disposePicking=installPilotPicking(c);
    const disposeLabels=hasLiveIncident(c)?installLiveIncidentLabels(c):()=>{};
    c.map.__visualDisposed=false;
    c.map.__visualWhenIdle=async()=>{
        while(c.map.__visualInteracting&&!c.map.__visualDisposed)await new Promise(resolve=>requestAnimationFrame(resolve));
        let current;do{current=mapQueues.get(c.map);await current;}while(current!==mapQueues.get(c.map));
    };
    c.map.__visualDispose=()=>{
        c.map.__visualDisposed=true;clearTimeout(pending);observer.disconnect();disposePicking();disposeInteraction();disposeLabels();
        const frames=liveFrames.get(c.map);if(frames)frames.pending=null;
        delete c.map.__visualWhenIdle;
        return Promise.resolve(mapQueues.get(c.map));
    };
}
function installPilotInteraction(c,clock,resize){
    const plot=c.map,caption=clock.querySelector('small');
    const live=hasLiveIncident(c);
    if(live){plot.tabIndex=-1;plot.setAttribute('aria-label','사건 지도 · 스페이스바로 재생 또는 일시정지');}
    let pointer=false,wheel=false,epoch=0,wheelTimer,firstFrame,secondFrame;
    const clearFrames=()=>{cancelAnimationFrame(firstFrame);cancelAnimationFrame(secondFrame);};
    const start=()=>{
        epoch++;clearFrames();plot.__visualInteracting=true;plot.dataset.interacting='true';
        caption.textContent=live?'비행 기록 · 시점 조작 중':'시점 조정 중';caption.style.display='block';clock.title=live?'시점을 조작하는 동안에도 비행 기록을 계속 재생합니다.':'놓으면 현재 재생 시각을 반영합니다.';
    };
    const finish=()=>{
        if(pointer||wheel||plot.__visualDisposed)return;
        const token=++epoch;clearFrames();
        firstFrame=requestAnimationFrame(()=>{secondFrame=requestAnimationFrame(async()=>{
            if(live){
                if(token!==epoch||pointer||wheel||plot.__visualDisposed||!plot.isConnected)return;
                // Persist the user's last camera without another relayout. Trace
                // updates continue throughout the gesture and must not replay an
                // older camera when a pointer is released.
                rememberIncidentCamera(plot);
                plot.__visualInteracting=false;plot.dataset.interacting='false';
                caption.textContent='비행 기록';caption.style.display='';clock.title='';
                if(plot.__visualResizePending){plot.__visualResizePending=false;resize();}
                return;
            }
            await Promise.resolve(c.work).catch(()=>{});
            if(token!==epoch||pointer||wheel||plot.__visualDisposed||!plot.isConnected)return;
            const releasedCamera=plot._fullLayout?.scene?._scene?.getCamera?.();
            await mapMutation(plot,async()=>{
                if(token!==epoch||pointer||wheel||plot.__visualDisposed)return;
                if(releasedCamera)await window.Plotly.relayout(plot,{'scene.camera':copy(releasedCamera)});
            });
            if(token!==epoch||pointer||wheel||plot.__visualDisposed)return;
            plot.__visualInteracting=false;plot.dataset.interacting='false';
            caption.textContent='비행 기록';caption.style.display='';clock.title='';
            if(plot.__visualResizePending){plot.__visualResizePending=false;resize();}
            plot.__visualResume?.();
        });});
    };
    const down=event=>{
        if(event.button===0&&event.isPrimary!==false){
            if(live&&!event.target.closest('button,input,select,textarea,a,[contenteditable="true"],[role="slider"]'))plot.focus({preventScroll:true});
            pointer=true;start();
        }
    };
    const up=()=>{if(pointer){pointer=false;finish();}};
    const scroll=()=>{
        wheel=true;plot.__visualManualView=true;start();clearTimeout(wheelTimer);
        wheelTimer=setTimeout(()=>{wheel=false;finish();},180);
    };
    const blur=()=>{pointer=false;wheel=false;clearTimeout(wheelTimer);finish();};
    plot.addEventListener('pointerdown',down,true);plot.addEventListener('pointercancel',up,true);
    plot.addEventListener('wheel',scroll,{passive:true,capture:true});
    window.addEventListener('pointerup',up);window.addEventListener('blur',blur);
    return ()=>{
        epoch++;clearFrames();clearTimeout(wheelTimer);pointer=wheel=false;plot.__visualInteracting=false;
        plot.removeEventListener('pointerdown',down,true);plot.removeEventListener('pointercancel',up,true);
        plot.removeEventListener('wheel',scroll,true);window.removeEventListener('pointerup',up);window.removeEventListener('blur',blur);
        delete plot.__visualResume;
    };
}
function rememberIncidentCamera(map){
    const scene=map._fullLayout?.scene?._scene;
    // This is Plotly's own pointer/wheel commit operation. Orthographic zoom
    // changes aspectratio as well as the camera, so preserve both before restyle.
    if(scene?.saveLayout&&map.layout){scene.saveLayout(map.layout);return;}
    const live=scene?.getCamera?.();
    if(!live)return;
    // Plotly's camera controller owns the gesture. Only synchronize its public
    // layout snapshot before a trace restyle; never invoke camera.setCamera or
    // relayout while the user is moving it.
    if(map.layout?.scene)map.layout.scene.camera=copy(live);
    if(map._fullLayout?.scene)map._fullLayout.scene.camera=copy(live);
}
function installLiveIncidentLabels(c){
    const plot=c.map,host=plot.parentElement;
    const layer=document.createElement('div');layer.className='incident-live-labels';
    layer.setAttribute('aria-hidden','true');
    Object.assign(layer.style,{position:'absolute',inset:'0',pointerEvents:'none',zIndex:'2',overflow:'hidden'});
    host.append(layer);
    const labels=new Map();let frame,disposed=false,lastKey='';
    plot.__presentationLabels=[];
    const tick=()=>{
        if(disposed||plot.__visualDisposed||!plot.isConnected)return;
        frame=requestAnimationFrame(tick);
        const nodes=plot.__presentationLabels||[],params=plot._fullLayout?.scene?._scene?.glplot?.cameraParams;
        if(!params)return;
        const key=JSON.stringify([nodes,params.model,params.view,params.projection,plot.clientWidth,plot.clientHeight]);
        if(key===lastKey)return;lastKey=key;
        const projected=projectedMainFleet(plot,nodes)||[],keep=new Set(nodes.map(n=>n.id));
        for(const [id,el] of labels)if(!keep.has(id)){el.remove();labels.delete(id);}
        const bounds=plot.getBoundingClientRect(),hostBounds=host.getBoundingClientRect();
        nodes.forEach((node,i)=>{
            let el=labels.get(node.id);
            if(!el){el=document.createElement('span');el.dataset.drone=node.id;
                Object.assign(el.style,{position:'absolute',font:'700 12px Arial,sans-serif',color:'#f0f8ff',
                    background:'rgba(15,33,49,.96)',border:'1px solid',padding:'4px',whiteSpace:'nowrap',
                    pointerEvents:'none',transform:'translate(-50%,-100%)',boxShadow:'0 2px 5px #0003'});
                layer.append(el);labels.set(node.id,el);}
            el.textContent=node.name;el.style.borderColor=node.color;
            const point=projected.find(p=>p.id===node.id);
            const visible=point&&point.depth>=-1&&point.depth<=1&&point.x>=0&&point.x<=bounds.width&&point.y>=0&&point.y<=bounds.height;
            el.hidden=!visible;if(!visible)return;
            const offset=node.side==='top left'?-19:node.side==='top right'?19:12;
            const x=Math.max(22,Math.min(bounds.width-22,point.x+offset));
            const y=Math.max(30,Math.min(bounds.height-8,point.y-15));
            el.style.left=(bounds.left-hostBounds.left+x)+'px';el.style.top=(bounds.top-hostBounds.top+y)+'px';
        });
    };
    frame=requestAnimationFrame(tick);
    return ()=>{disposed=true;cancelAnimationFrame(frame);layer.remove();delete plot.__presentationLabels;delete plot.__presentationIncident;};
}
function installPilotPicking(c){
    const plot=c.map,lookup=new Map(c.run.manifest.fleet.map(node=>[node.id,node]));
    let origin=null,gesture=0,downAt=-Infinity,nativeAt=-Infinity,acceptedGesture=-1,acceptedAt=-Infinity,pending;
    const nearest=(clientX,clientY)=>{
        if(plot.__visualDisposed||!plot._fullLayout)return null;
        const trace=plot.data?.find(row=>row.meta?.role==='drones');
        if(!trace||trace.visible===false)return null;
        const nodes=(trace.x||[]).map((x,i)=>({id:trace.customdata?.[i],x,y:trace.y[i],z:trace.z[i],description:trace.hovertext?.[i]}))
            .filter(node=>lookup.has(node.id)&&[node.x,node.y,node.z].every(Number.isFinite));
        const points=projectedMainFleet(plot,nodes),box=plot.getBoundingClientRect();
        let match=null,distance=14;
        for(const point of points||[]){
            if(point.x<0||point.x>box.width||point.y<0||point.y>box.height)continue;
            const d=Math.hypot(clientX-box.left-point.x,clientY-box.top-point.y);
            if(d<=distance){distance=d;match=nodes.find(node=>node.id===point.id);}
        }
        return match;
    };
    // Root's existing selection handler calls this after verifying a known ID.
    // Native picking and the projected fallback may both fire for one gesture.
    plot.__visualAcceptPick=event=>{
        const now=performance.now();
        const key=event.__visualFallback?event.__visualGesture:gesture;
        if(now-downAt<700&&acceptedGesture===key&&now-acceptedAt<700)return false;
        if(!event.__visualFallback)nativeAt=now;
        acceptedGesture=key;acceptedAt=now;
        return true;
    };
    const down=event=>{
        if(event.button!==0||event.isPrimary===false)return;
        clearTimeout(pending);gesture++;downAt=performance.now();
        origin={x:event.clientX,y:event.clientY,id:event.pointerId,gesture,at:downAt,moved:false};
    };
    const move=event=>{
        if(origin&&Math.hypot(event.clientX-origin.x,event.clientY-origin.y)>4){origin.moved=true;plot.__visualManualView=true;}
        const node=origin?null:nearest(event.clientX,event.clientY);
        plot.title=node?`${displayDrone(node.id,c.run.manifest.fleet)} · ${node.description||'상태 미확인'} · 고도 ${node.z.toFixed(1)} m`:'';
    };
    const up=event=>{
        const tap=origin;origin=null;
        if(!tap||event.button!==0||event.pointerId!==tap.id||tap.moved||performance.now()-tap.at>600||Math.hypot(event.clientX-tap.x,event.clientY-tap.y)>4)return;
        const node=nearest(event.clientX,event.clientY);if(!node)return;
        pending=setTimeout(()=>{
            if(plot.__visualDisposed||!plot.isConnected||nativeAt>=tap.at||gesture!==tap.gesture)return;
            plot.emit?.('plotly_click',{__visualFallback:true,__visualGesture:tap.gesture,points:[{customdata:node.id,data:{meta:{role:'drones'}}}]});
        },24);
    };
    const cancel=()=>{origin=null;plot.title='';};
    plot.addEventListener('pointerdown',down,true);plot.addEventListener('pointermove',move,true);
    plot.addEventListener('pointerup',up,true);plot.addEventListener('pointercancel',cancel,true);plot.addEventListener('pointerleave',cancel);
    return ()=>{
        clearTimeout(pending);cancel();
        plot.removeEventListener('pointerdown',down,true);plot.removeEventListener('pointermove',move,true);
        plot.removeEventListener('pointerup',up,true);plot.removeEventListener('pointercancel',cancel,true);plot.removeEventListener('pointerleave',cancel);
        delete plot.__visualAcceptPick;
    };
}
function fitPatch(c,resetCamera){
    const layout=copy(c.spec.layout);frameBounds(c,layout);
    const patch={'scene.aspectmode':layout.scene.aspectmode,'scene.aspectratio':layout.scene.aspectratio};
    for(const name of ['xaxis','yaxis','zaxis'])patch['scene.'+name+'.range']=layout.scene[name].range;
    if(resetCamera)patch['scene.camera']=layout.scene.camera;
    return patch;
}
async function settledIncidentProjection(map){
    let previous=null,stable=0;
    for(let frame=0;frame<16;frame++){
        await new Promise(resolve=>requestAnimationFrame(resolve));
        if(map.__visualDisposed||!map.isConnected)return;
        const params=map._fullLayout?.scene?._scene?.glplot?.cameraParams;
        if(!params)continue;
        const values=[...params.model,...params.view,...params.projection];
        stable=previous&&values.every((v,i)=>Math.abs(v-previous[i])<1e-9)?stable+1:0;
        previous=values;if(stable>=3)return;
    }
}
async function fitIncidentCamera(Plot,c,preset=pilotCamera){
    const view=copy(preset),axes=['x','y','z'],center=view.center||{x:0,y:0,z:0};
    view.center={x:0,y:0,z:0};
    for(const axis of axes)view.eye[axis]-=center[axis]||0;
    const size=c.map.clientWidth+'|'+c.map.clientHeight;
    const key=size+'|'+JSON.stringify([axes.map(axis=>Number(view.eye[axis].toFixed(6))),view.up,view.projection]);
    const cache=c.map.__visualFitCache??=new Map(),cached=cache.get(key);
    const patch=fitPatch(c,true);
    let desiredCamera=copy(cached?.camera||view),desiredAspect=copy(cached?.aspect||patch['scene.aspectratio']);
    await Plot.relayout(c.map,{...patch,'scene.camera':desiredCamera,'scene.aspectratio':desiredAspect});
    c.map.__visualFitSize=c.map.clientWidth+'|'+c.map.clientHeight;
    const nodes=[];
    for(const sample of c.samples)for(const point of sample.p.values())
        if([point.x,point.y,point.z].every(Number.isFinite))nodes.push(point);
    for(let attempt=0;attempt<5;attempt++){
        await settledIncidentProjection(c.map);
        if(c.map.__visualDisposed||c.map.__visualManualView||c.map.__visualInteracting||!c.map.isConnected)return;
        const projected=projectedMainFleet(c.map,nodes);if(!projected?.length)return;
        const width=c.map.clientWidth,height=c.map.clientHeight;
        const usableWidth=Math.max(100,width-60),usableHeight=Math.max(140,height-110);
        const left=Math.min(...projected.map(p=>p.x)),right=Math.max(...projected.map(p=>p.x));
        const top=Math.min(...projected.map(p=>p.y)),bottom=Math.max(...projected.map(p=>p.y));
        const params=c.map._fullLayout?.scene?._scene?.glplot?.cameraParams;if(!params)return;
        const footprint=Math.max((right-left)/usableWidth,(bottom-top)/usableHeight);
        const scale=footprint>1e-5&&(footprint<.61||footprint>.71)?Math.max(.2,Math.min(5,.66/footprint)):1;
        const dx=(((left+right)/2-width/2)*2/width)/params.projection[0];
        const dy=((height/2+12-(top+bottom)/2)*2/height)/params.projection[5];
        if(Math.abs(scale-1)<.001&&Math.abs(dx)<.006&&Math.abs(dy)<.006){cache.set(key,{camera:copy(desiredCamera),aspect:copy(desiredAspect)});return;}
        const shift=[0,1,2].map(i=>params.view[i*4]*dx+params.view[i*4+1]*dy);
        for(let i=0;i<3;i++){
            const axis=axes[i],offset=desiredCamera.eye[axis]-desiredCamera.center[axis];
            desiredCamera.center[axis]=(desiredCamera.center[axis]+shift[i])*scale;desiredCamera.eye[axis]=desiredCamera.center[axis]+offset;
        }
        desiredAspect={x:desiredAspect.x*scale,y:desiredAspect.y*scale,z:desiredAspect.z*scale};
        await Plot.relayout(c.map,{'scene.camera':copy(desiredCamera),'scene.aspectratio':copy(desiredAspect)});
    }
    await settledIncidentProjection(c.map);
    const final=projectedMainFleet(c.map,nodes);
    if(final?.every(point=>point.x>=0&&point.x<=c.map.clientWidth&&point.y>=0&&point.y<=c.map.clientHeight&&point.depth>=-1&&point.depth<=1))
        cache.set(key,{camera:copy(desiredCamera),aspect:copy(desiredAspect)});
}
export function focusIncidentView(Plot,c,preset=pilotCamera){
    if(!hasVisualScene(c))return Promise.resolve();
    c.map.__visualManualView=false;
    return mapMutation(c.map,()=>fitIncidentCamera(Plot,c,preset));
}
function observedArrow(tr,c,action) {
    const from=pointsAt(c,action.time_s)?.get(action.relay);
    const to=pointsAt(c,action.outcome_available_s)?.get(action.relay);
    if(!from||!to)return;
    const dx=to.x-from.x,dy=to.y-from.y,length=Math.hypot(dx,dy);
    if(length<.01)return;
    segment(tr,from,to,action.relay);
    const wing=Math.min(length*.65,9)/length;
    for(const side of [-.72,.72]){
        segment(tr,to,{x:to.x-wing*(dx+side*dy),y:to.y-wing*(dy-side*dx),z:to.z},action.relay);
    }
}
function labelPositions(nodes) {
    // Stagger above-labels for close neighbours in the initial north-up view.
    return nodes.map((a,i)=>{
        let nearest=null,distance=18;
        for(let j=0;j<nodes.length;j++){
            if(i===j)continue;
            const b=nodes[j],d=Math.hypot(a.x-b.x,a.y-b.y);
            if(d<distance){nearest=b;distance=d;}
        }
        if(!nearest)return 'top center';
        return (a.x<nearest.x||(a.x===nearest.x&&a.y<nearest.y))?'top left':'top right';
    });
}
export function visibleIncidentMovements(event,t){
    if(!event||!Number.isFinite(t)||t<event.start_s)return [];
    const boundaries=[event.recovery_s,event.end_s].filter(Number.isFinite);
    if(!boundaries.length||t>Math.min(...boundaries))return [];
    const completed=(event.interventions||[]).filter(action=>
        Number.isFinite(action.time_s)&&action.time_s>=event.start_s&&
        Number.isFinite(action.outcome_available_s)&&action.outcome_available_s>=action.time_s&&
        action.outcome_available_s<=t&&action.moved_m>0);
    const latest=Math.max(...completed.map(action=>action.outcome_available_s));
    return completed.filter(action=>action.outcome_available_s===latest);
}
function layersAt(c,t) {
    const e=c.event, pts=pointsAt(c,t);
    const rows={};
    for(const tr of c.map.data){
        const role=tr.meta?.role;
        if(role!=='buildings'&&role!=='buildingOutlines'&&!tr.meta?.visual_static)rows[role]={x:[],y:[],z:[],customdata:[],text:[]};
    }
    if(!pts)return rows;
    const fleet=c.run.manifest.fleet;
    const lookup=new Map(fleet.map(node=>[node.id,node]));
    const frame=intervalAt(c.run.overview.rf_frames,t);
    const communication=commAt(c.run,frame,t);
    for(const node of pts.values()){
        const role=lookup.get(node.id)?.role||node.role;
        const tr=rows.drones;
        xyz(tr,node,node.id);tr.text.push(displayDrone(node.id,fleet));
        const paint=aircraftPaint(communication,node.id);
        (tr.colors??=[]).push(paint.color);
        const full=(role==='search'?'수색기':role==='relay'?'중계기':'드론')+' · '+paint.description;
        (tr.hovertext??=[]).push(full);
        if(c.trackedId===node.id)xyz(rows.selectedHalo,node,node.id);
    }
    if(c.trackedId){
        let previous;
        for(const sample of c.samples){
            if(sample.time>t)break;
            const next=sample.p.get(c.trackedId);
            if(previous&&next)segment(rows.trackedPath,previous,next,c.trackedId);
            previous=next;
        }
    }
    const active=new Set((frame?.links||[]).filter(l=>l.available).map(l=>edgeKey(l.source,l.target)));
    const addEdge=(role,a,b)=>segment(rows[role],pts.get(a),pts.get(b),[a,b]);
    const completed=visibleIncidentMovements(e,t);
    const rec=e?.recovery_s;
    for(const action of completed)observedArrow(rows.actualTrack,c,action);
    // Retain the old three-second movement emphasis for network overlays;
    // only the recorded movement arrow itself persists to the event boundary.
    const latest=completed[0]?.outcome_available_s;
    const movingDuringOutage=completed.length>0&&t-latest<3&&(!Number.isFinite(rec)||t<rec);
    if(e&&t>=e.start_s&&t<e.end_s&&!movingDuringOutage){
        for(const [a,b] of e.crossing_lost_links||[]){
            if(!active.has(edgeKey(a,b)))addEdge('lostLinks',a,b);
        }
    }else if(Number.isFinite(rec)&&t>=rec&&frame?.metrics?.search_connected){
        const nextOutage=(e.window_outages||[]).some(([start])=>start>=rec&&start<=t);
        if(!nextOutage){
            for(const path of c.routes?.paths||[]){
                // Don't keep drawing a green route once any of its original links drops.
                if(!path.slice(1).every((id,i)=>active.has(edgeKey(path[i],id))))continue;
                for(let i=1;i<path.length;i++)addEdge('recoveryPath',path[i-1],path[i]);
            }
        }
    }
    return rows;
}
export function updateIncidentMap(Plot,c,t) {
    if(hasLiveIncident(c)){
        let state=liveFrames.get(c.map);
        if(!state){state={pending:null,work:null,requested:0,coalesced:0,rendered:0,duringGesture:0};liveFrames.set(c.map,state);}
        state.requested++;if(state.pending)state.coalesced++;
        state.pending={c,t};
        if(state.work)return state.work;
        state.work=mapMutation(c.map,async()=>{
            while(state.pending&&!c.map.__visualDisposed&&c.map.isConnected){
                const latest=state.pending;state.pending=null;
                await applyIncidentMap(Plot,latest.c,latest.t);
                if(c.map.__visualDisposed||!c.map.isConnected)return;
                state.rendered++;if(c.map.__visualInteracting)state.duringGesture++;
                c.map.__presentationIncident={context:latest.c,time:latest.t,requested:state.requested,
                    rendered:state.rendered,coalesced:state.coalesced,duringGesture:state.duringGesture};
            }
        }).then(()=>{
            state.work=null;
            // A request may arrive in the promise handoff after the drain's last
            // iteration. Join its newest frame instead of leaving it stranded.
            if(state.pending&&!c.map.__visualDisposed&&c.map.isConnected){
                const latest=state.pending;state.pending=null;
                return updateIncidentMap(Plot,latest.c,latest.t);
            }
        },error=>{state.work=null;state.pending=null;throw error;});
        return state.work;
    }
    return mapMutation(c.map,()=>c.map.__visualInteracting?undefined:applyIncidentMap(Plot,c,t));
}
async function applyIncidentMap(Plot,c,t) {
    const rows=layersAt(c,t), indices=[];
    const updates={x:[],y:[],z:[],customdata:[]};
    for(let i=0;i<c.map.data.length;i++){
        const role=c.map.data[i].meta.role, r=rows[role];
        if(!r)continue;
        indices.push(i);
        for(const field of Object.keys(updates))updates[field].push(r[field]);
    }
    const droneIdx=c.map.data.findIndex(tr=>tr.meta?.role==='drones');
    // Undefined skips the non-drone traces; null would erase Python-authored styles.
    const droneField=(key)=>indices.map(i=>i===droneIdx?rows.drones[key]:undefined);
    updates.text=droneField('text');
    updates.hovertext=droneField('hovertext');
    updates['marker.color']=droneField('colors');
    const marks=rows.drones;
    // Keep all aircraft in the marker trace for hovering and picking. Only the
    // current stage and the user's individually selected aircraft need map labels.
    const selected=new Set(c.focus?.activeIds||[]);
    for(const id of rows.actualTrack.customdata)if(typeof id==='string')selected.add(id);
    if(c.trackedId)selected.add(c.trackedId);
    const named=[];
    marks.customdata.forEach((id,i)=>{
        if(selected.has(id))named.push({id,x:marks.x[i],y:marks.y[i],z:marks.z[i],name:marks.text[i],color:marks.colors[i]});
    });
    const placements=labelPositions(named);
    const labels=named.map((point,i)=>{
        const side=placements[i];
        const offset=side==='top left'?-15:side==='top right'?15:0;
        const label={x:point.x,y:point.y,z:point.z,text:'<b>'+point.name+'</b>',showarrow:false,
            xshift:offset,yshift:13,opacity:.97,bgcolor:'rgba(255,255,255,.80)',borderpad:1,
            captureevents:false,font:{family:'Arial, sans-serif',size:12,color:'#17334b'}};
        if(hasVisualScene(c))Object.assign(label,{showarrow:true,arrowhead:0,arrowwidth:1,arrowcolor:point.color,
            ax:offset||12,ay:-28,xshift:0,yshift:0,opacity:1,bgcolor:'rgba(15,33,49,.96)',bordercolor:'#405d73',borderwidth:1,borderpad:4,
            font:{family:'Arial, sans-serif',size:12,color:'#f0f8ff'}});
        return label;
    });
    if(hasLiveIncident(c)){
        rememberIncidentCamera(c.map);
        // No layout patch on the replay path: annotations are a separate DOM
        // layer, so moving aircraft does not reset an in-progress camera gesture.
        await Plot.restyle(c.map,updates,indices);
        if(c.map.__visualDisposed||!c.map.isConnected)return;
        c.map.__presentationLabels=named.map((point,i)=>({...point,side:placements[i]}));
    }else{
        await Plot.update(c.map,updates,{'scene.annotations':labels},indices);
    }
    if(hasVisualScene(c)){const output=c.map.parentElement.querySelector('.incident-map-clock output');if(output)output.textContent=t.toLocaleString('ko-KR',{minimumFractionDigits:1,maximumFractionDigits:1})+' s';}
    c.map.dataset.renderedCursor=String(t);
    const frame=intervalAt(c.run.overview.rf_frames,t);
    selectedReadout(document.getElementById('incidentSelectedStatus'),c.trackedId?commAt(c.run,frame,t):null,c.trackedId);
}
export function resetIncidentView(Plot,c) {
    if(hasVisualScene(c)){
        c.map.parentElement.querySelector('#incidentCameraTop')?.setAttribute('aria-pressed','false');
        c.map.parentElement.querySelector('#incidentCameraOblique')?.setAttribute('aria-pressed','true');
    }
    return hasVisualScene(c)?focusIncidentView(Plot,c):mapMutation(c.map,()=>Plot.relayout(c.map,{'scene.camera':copy(camera)}));
}
