import {intervalAt} from './render.js';
import {droneMapLabel} from './map3d.js';

const navy = '#14609a';
const ochre = '#a34d0a';
const plotOptions = {responsive:true,displayModeBar:false,displaylogo:false,scrollZoom:false};
const formatTime = value => Number(value.toFixed(2)).toLocaleString('ko-KR',{maximumFractionDigits:2})+'초';
const finite = Number.isFinite;
const ordered = (a,b) => a.localeCompare(b,'en',{numeric:true});
const pairKey = ([a,b]) => [a,b].sort(ordered).join('|');

function element(tag,className,text) {
    const el=document.createElement(tag);
    if(className) el.className=className;
    if(text!==undefined) el.textContent=text;
    return el;
}

function shortestPath(adjacency,start,end) {
    const paths=[[start]],seen=new Set([start]);
    for(let i=0;i<paths.length;i++){
        const path=paths[i];
        for(const next of adjacency.get(path.at(-1))||[]){
            if(seen.has(next))continue;
            const extended=[...path,next];
            if(next===end)return extended;
            seen.add(next);
            paths.push(extended);
        }
    }
    return [];
}

// Search groups may change during an outage. Use the last down frame for restoration.
export function recoveryRoutes(event,frames,fleet) {
    const paths=[], gained=new Map();
    if(!finite(event.recovery_s))return {paths,edges:[]};
    const before=intervalAt(frames,event.recovery_s-.5);
    const after=intervalAt(frames,event.recovery_s);
    if(!before||!after)return {paths,edges:[]};
    const searches=new Set(fleet?.length?fleet.filter(d=>d.role==='search').map(d=>d.id):event.search_groups.flat());
    const groups=before.components.map(g=>g.filter(n=>searches.has(n)).sort(ordered)).filter(g=>g.length).sort((a,b)=>ordered(a[0],b[0]));
    const old=new Set(before.links.filter(l=>l.available).map(l=>pairKey([l.source,l.target])));
    const neighbors=new Map();
    for(const link of after.links){
        if(!link.available)continue;
        for(const [a,b] of [[link.source,link.target],[link.target,link.source]]){
            if(!neighbors.has(a))neighbors.set(a,[]);
            neighbors.get(a).push(b);
        }
    }
    for(const peers of neighbors.values())peers.sort(ordered);
    for(const group of groups.slice(1)){
        const path=shortestPath(neighbors,groups[0][0],group[0]);
        if(!path.length)continue;
        paths.push(path);
        for(let i=1;i<path.length;i++){
            const key=pairKey([path[i-1],path[i]]);
            if(!old.has(key))gained.set(key,key.split('|'));
        }
    }
    return {paths,edges:[...gained.values()]};
}

function lastIndex(times,t){
    let a=0,b=times.length;
    while(a<b){
        const mid=Math.floor((a+b)/2);
        if(times[mid]<=t)a=mid+1;
        else b=mid;
    }
    return a-1;
}

function pairIntervals(frames,key,start,end){
    const intervals=[];
    for(const f of frames){
        const left=Math.max(start,f.interval_start_s),right=Math.min(end,f.interval_end_s);
        if(right<=left)continue;
        const link=f.links.find(l=>pairKey([l.source,l.target])===key);
        const state=link?Boolean(link.available):null;
        const last=intervals.at(-1);
        if(last&&last.state===state&&last.end===left)last.end=right;
        else intervals.push({start:left,end:right,state});
    }
    return intervals;
}

export function networkOutageBands(frames,event){
    const bands=[];
    const [start,end]=event.window;
    for(const frame of frames){
        const connected=frame.metrics?.search_connected;
        if(connected!==0&&connected!==false)continue;
        const left=Math.max(start,frame.interval_start_s),right=Math.min(end,frame.interval_end_s);
        if(right<=left)continue;
        const selected=left>=event.start_s&&left<event.end_s;
        const previous=bands.at(-1);
        if(previous&&previous.end===left&&previous.selected===selected)previous.end=right;
        else bands.push({start:left,end:right,selected});
    }
    return bands;
}

function traceFor(samples,metric,window,dir,name){
    const basis=samples?.traces?.[metric];
    const x=[],y=[],customdata=[];
    // Python authors true receive measurements. The measured and available times differ.
    if(basis){
        for(let i=0;i<basis.x.length;i++){
            const measured=basis.x[i],available=basis.customdata[i];
            if(measured<window[0]||measured>window[1]||available<window[0]||available>window[1])continue;
            x.push(measured);y.push(basis.y[i]);customdata.push(available);
        }
    }
    return {...basis,type:'scatter',mode:'markers',x,y,customdata,name,showlegend:false,
        connectgaps:false,marker:{size:6.5,color:dir===0?navy:ochre,symbol:dir===0?'circle':'diamond',line:{color:'white',width:.6}},
        hovertemplate:name+' · %{y:.1f} '+(metric==='sinr'?'dB':'dBm')+'<br>측정 %{x:.2f}초 · 기록 %{customdata:.1f}초<extra></extra>'};
}

function bindSeeking(plot,seek){
    let picked=false;
    plot.on('plotly_click',evt=>{
        const availability=evt.points?.[0]?.customdata;
        if(!finite(availability))return;
        picked=true;seek(availability);
        setTimeout(()=>{picked=false;},70);
    });
    plot.addEventListener('click',evt=>{
        const axis=plot._fullLayout?.xaxis;
        if(!axis||!evt.target.closest('.nsewdrag,.scatterlayer,.draglayer'))return;
        const x=evt.clientX-plot.getBoundingClientRect().left-axis._offset;
        if(x<0||x>axis._length)return;
        const t=axis.p2d(x);
        setTimeout(()=>{if(!picked&&plot.isConnected)seek(t);},0);
    });
}

function observationText(source,metric,t,start){
    if(!source)return '유효 관측 없음';
    const i=lastIndex(source.t,t);
    if(i<0||source.t[i]<start||!finite(source[metric][i])||!finite(source.m[i]))return '유효 관측 없음';
    const age=Math.max(0,t-source.m[i]);
    return source[metric][i].toFixed(1)+(metric==='sinr'?' dB':' dBm')+' · '+formatTime(age)+' 전';
}

export function createIncidentRadio(host,{event,signals,template,frames,fleet,seek,routes}){
    const P=window.Plotly,aliases=new Map(fleet.map(node=>[node.id,droneMapLabel(node.id,fleet)]));
    const short=id=>aliases.get(id)||id;
    const pretty=key=>key.split('|').map(short).join(' ↔ ');
    const full=key=>key.split('|').map(id=>short(id)+' = 드론 '+id.replace('uav-','')).join(' · ');
    const losses=[...new Set(event.crossing_lost_links.map(pairKey))].sort(ordered);
    const recovery=routes||recoveryRoutes(event,frames,fleet);
    const restored=[...new Set(recovery.edges.map(pairKey))].sort(ordered);
    const windowFrames=frames.filter(f=>f.interval_start_s<event.window[1]&&f.interval_end_s>event.window[0]);
    const networkBands=networkOutageBands(windowFrames,event);
    const cards=new Map();
    let disposed=false, clock=event.window[0], metric='sinr',selectedOther='',redraw=true;
    let working=null, dirty=false;
    const heading=element('header','ir-heading');
    const title=element('h3','', '관련 드론 쌍의 수신 신호');
    const unitControl=element('label','ir-metric-label','신호 지표');
    const field=element('select');field.id='incidentMetric';
    field.add(new Option('신호 대 간섭·잡음비 (dB)','sinr'));
    field.add(new Option('수신전력 (dBm)','power'));
    unitControl.append(field);heading.append(title,unitControl);
    const legend=element('div','ir-key');
    for(const [kind,text] of [['network','수색망 단절'],['selected','선택한 단절'],['up','두 기체 직접 연결'],['down','두 기체 직접 단절']]){
        const item=element('span','ir-key-item',text);item.dataset.kind=kind;legend.append(item);
    }
    const info=element('span','ir-info','점은 실제 수신 · 빈 구간은 새 측정 없음');legend.append(info);
    const explanation=element('p','ir-explanation','아래 띠는 두 기체의 직접 링크입니다. 직접 링크가 끊겨도 다른 기체를 거쳐 수색망이 이어질 수 있습니다.');
    const groups=element('div','ir-groups');
    const extraDetails=element('details','ir-extras');extraDetails.id='incidentRadioExtras';
    extraDetails.append(element('summary','', '다른 드론 쌍 살펴보기'));
    const extraContent=element('div','ir-extra-content');
    const extraLabel=element('label','ir-extra-label','추가할 드론 쌍');
    const extra=element('select');
    extra.add(new Option('드론 쌍 선택',''));
    const primary=new Set([...losses,...restored]);
    const allKeys=[...new Set(Object.keys(signals).map(s=>pairKey(s.split('|'))))].sort(ordered);
    for(const key of allKeys)if(!primary.has(key))extra.add(new Option(pretty(key),key));
    extraLabel.append(extra);extraContent.append(extraLabel);extraDetails.append(extraContent);
    host.replaceChildren(heading,legend,explanation,groups,extraDetails);
    function createGroup(group,label){
        const wrap=element('section','ir-group');
        wrap.dataset.group=group;
        wrap.append(element('h4','ir-group-title',label));
        groups.append(wrap);
        return wrap;
    }
    function addCard(key,group,wrap){
        const article=element('article','incident-pair-card');
        article.tabIndex=-1;
        article.dataset.pair=key;article.dataset.group=group;
        if(restored.includes(key))article.dataset.restores='true';
        const [a,b]=key.split('|');
        const head=element('div','ir-pair-head');
        const pairTitle=element('strong','ir-pair-name',pretty(key));pairTitle.title=full(key);
        head.append(pairTitle);
        const names=[short(a)+' → '+short(b),short(b)+' → '+short(a)];
        const values=names.map((name,i)=>{
            const dir=element('span','ir-direction');
            dir.dataset.dir=i;dir.append(element('b','',name));
            const value=element('span','ir-last-value');
            dir.append(value);head.append(dir);return value;
        });
        const plot=element('div','incident-pair-plot');
        if(cards.size===0)plot.id='incidentSignal';
        plot.setAttribute('aria-label',pretty(key)+' 양방향 수신 측정 그래프');
        article.append(head,plot);wrap.append(article);
        const card={key,article,plot,values,names,sources:[signals[b+'|'+a],signals[a+'|'+b]],
                    intervals:pairIntervals(windowFrames,key,...event.window),plotted:false};
        cards.set(key,card);
        return card;
    }
    const lossGroup=createGroup('loss','단절 시작에 끊긴 링크 · '+formatTime(event.start_s));
    for(const key of losses)addCard(key,'loss',lossGroup);
    if(!losses.length)lossGroup.append(element('p','ir-notice','직전 프레임에서 사라진 그룹 간 링크가 확인되지 않았습니다.'));
    if(finite(event.recovery_s)&&restored.length){
        const recoveryGroup=createGroup('restore','재연결 경로에 새로 열린 링크 · '+formatTime(event.recovery_s));
        for(const key of restored){
            if(!cards.has(key)){addCard(key,'restore',recoveryGroup);continue;}
            const jump=element('button','ir-reference',pretty(key)+' · 위 그래프 보기');
            jump.type='button';jump.dataset.pair=key;
            jump.onclick=()=>{cards.get(key)?.article.focus();};
            recoveryGroup.append(jump);
        }
    }else if(event.right_censored){
        groups.append(element('p','ir-notice','실행 종료까지 수색망 재연결 기록이 없습니다.'));
    }
    function removeExtra(){
        const card=cards.get(selectedOther);
        if(card){
            cards.delete(selectedOther);
            if(card.plotted)P.purge(card.plot);
            card.article.remove();
        }
        selectedOther='';
    }
    extra.onchange=async()=>{
        if(working)await working;
        if(disposed)return;
        removeExtra();
        if(extra.value){
            selectedOther=extra.value;
            addCard(selectedOther,'extra',extraContent);
        }
        redraw=true;draw();
    };
    extraDetails.ontoggle=async()=>{
        if(extraDetails.open||!selectedOther)return;
        if(working)await working;
        if(disposed||extraDetails.open)return;
        extra.value='';removeExtra();redraw=true;draw();
    };
    field.onchange=()=>{metric=field.value;redraw=true;draw();};

    function figuresFor(metricKey,t){
        const rows=[];
        let lo=Infinity,hi=-Infinity;
        for(const card of cards.values()){
            const data=card.sources.map((source,i)=>traceFor(source,metricKey,event.window,i,card.names[i]));
            for(const trace of data)for(const value of trace.y){lo=Math.min(value,lo);hi=Math.max(value,hi);}
            rows.push({card,data});
        }
        const pad=Math.max(2,(hi-lo)*.08);
        const yRange=finite(lo)?[Math.floor(lo-pad),Math.ceil(hi+pad)]:metricKey==='sinr'?[0,20]:[-100,-60];
        return rows.map(({card,data})=>{
            const layout=structuredClone(template.layout);
            layout.margin={l:46,r:14,t:6,b:30};
            layout.paper_bgcolor='#fff';layout.plot_bgcolor='#fbfcfd';
            layout.xaxis={...layout.xaxis,title:{text:''},anchor:'free',position:0,range:event.window.slice(),nticks:7,ticksuffix:'초',fixedrange:true,showline:false};
            layout.yaxis={...layout.yaxis,title:{text:metricKey==='sinr'?'dB':'dBm',standoff:1},range:yRange,nticks:3,domain:[.18,1],fixedrange:true};
            layout.shapes=[
                {type:'line',xref:'x',yref:'paper',x0:t,x1:t,y0:0,y1:1,line:{color:'#263e51',width:1.7}},
                ...networkBands.map(({start,end,selected})=>({type:'rect',xref:'x',yref:'paper',
                    x0:start,x1:end,y0:.18,y1:1,layer:'below',fillcolor:selected?'#fae6d1':'#f7ede1',
                    line:{width:selected?1.2:0,color:'#ad6b36'}})),
                ...card.intervals.map(({start,end,state})=>({type:'rect',xref:'x',yref:'paper',x0:start,x1:end,y0:.055,y1:.125,layer:'below',line:{width:0},
                    fillcolor:state===null?'#e1e4e8':state?'#79b2a5':'#bc504a'}))
            ];
            layout.annotations=data.every(tr=>!tr.x.length)?[{text:'양방향 새 측정점 없음',x:.5,y:.55,xref:'paper',yref:'paper',showarrow:false}]:[];
            return {card,data,layout};
        });
    }
    async function render(){
        while(dirty&&!disposed){
            dirty=false;
            const t=clock,key=metric,fullRender=redraw;
            redraw=false;
            for(const card of cards.values()){
                card.values.forEach((el,i)=>{el.textContent=observationText(card.sources[i],key,t,event.window[0]);});
            }
            if(fullRender){
                await Promise.all(figuresFor(key,t).map(async({card,data,layout})=>{
                    if(card.plotted)await P.react(card.plot,data,layout,plotOptions);
                    else{
                        await P.newPlot(card.plot,data,layout,plotOptions);
                        card.plotted=true;
                        if(!disposed)bindSeeking(card.plot,seek);
                    }
                }));
            }else{
                await Promise.all([...cards.values()].map(card=>P.relayout(card.plot,{'shapes[0].x0':t,'shapes[0].x1':t})));
            }
        }
    }
    function draw(){
        if(disposed)return Promise.resolve();
        dirty=true;
        if(!working)working=render().finally(()=>{working=null;});
        return working;
    }
    function dispose(){
        disposed=true;
        const cleanup=()=>{for(const {plot} of cards.values())if(plot?._fullLayout)P.purge(plot);cards.clear();};
        if(working)working.then(cleanup,cleanup);
        else cleanup();
    }
    const ready=draw();
    return {ready,setTime(t){clock=t;return draw();},dispose};
}
