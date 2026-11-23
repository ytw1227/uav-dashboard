import {droneMapLabel} from './map3d.js';
import {intervalAt} from './render.js';
import {detourText} from './comm_state.js';

const finite = Number.isFinite;
const plotOptions = {responsive:true,displayModeBar:false,displaylogo:false,scrollZoom:false};
const fields = [
    {key:'beacon_received_sinr_db',label:'비콘 신호 품질',unit:'dB',chart:true},
    {key:'beacon_received_power_dbm',label:'비콘 수신전력',unit:'dBm',chart:true},
    {key:'last_ack_sinr_db',label:'마지막 응답 신호 품질',unit:'dB',chart:true},
    {key:'last_ack_received_power_dbm',label:'마지막 응답 수신전력',unit:'dBm',chart:true},
    {key:'ack_success_ratio_in_interval',label:'구간 응답 성공률',unit:'%',chart:false}
];
const colors = ['#14609a','#a34d0a'];
const fmt = (value,digits=1) => finite(value) ? value.toLocaleString('ko-KR',{maximumFractionDigits:digits}) : '—';
const element = (tag,className,text) => {
    const el=document.createElement(tag);
    if(className)el.className=className;
    if(text!==undefined)el.textContent=text;
    return el;
};
const timeOf = row => row.available_at_s ?? row.observed_at_s;
const samePair = (link,a,b) => (link.source===a&&link.target===b)||(link.source===b&&link.target===a);

// The most recent available frame owns validity: never carry a valid old value
// past a later frame whose mask is false, and never expose a future frame.
export function flightRadioReading(rows,key,time) {
    let lo=0,hi=rows.length;
    while(lo<hi){const mid=(lo+hi)>>1;if(timeOf(rows[mid])<=time)lo=mid+1;else hi=mid;}
    const row=rows[lo-1];
    if(!row)return {valid:false,reason:'이 시각까지 관측 없음'};
    const value=row.values?.[key];
    if(row.valid?.[key]!==true||!finite(value))return {valid:false,reason:'유효 측정값 없음',available:timeOf(row)};
    const frame=row.observed_at_s ?? timeOf(row),storedAge=row.ages?.[key];
    const measured=finite(frame)&&finite(storedAge)&&storedAge>=0 ? frame-storedAge : null;
    const age=finite(measured) ? Math.max(0,time-measured) : null;
    return {valid:true,value,age,measured,available:timeOf(row)};
}

// Receive samples are positioned at their actual receive time, not at every
// 0.5 s copy of the latest value. First availability remains attached for seeking.
export function flightRadioSamples(rows,key) {
    const points=[],lastByValue=new Map();
    for(const row of rows){
        if(row.valid?.[key]!==true||!finite(row.values?.[key]))continue;
        const frame=row.observed_at_s ?? timeOf(row),age=row.ages?.[key],available=timeOf(row);
        if(!finite(frame)||!finite(age)||age<0||!finite(available))continue;
        const measured=frame-age,value=row.values[key];
        if(measured>available+1e-8)continue;
        // The logs carry float32 ages. Copies of one receive sample can differ
        // after subtracting an older, less precise age from a later frame.
        // Use only that quantization uncertainty, not a fixed time bucket.
        const ageError=Math.max(1e-8,2**(Math.floor(Math.log2(age||1))-24));
        const previous=lastByValue.get(value);
        if(previous&&Math.abs(previous.measured-measured)<=previous.ageError+ageError+1e-8)continue;
        const point={measured,available,value};
        lastByValue.set(value,{measured,ageError});points.push(point);
    }
    return points.sort((a,b)=>a.measured-b.measured||a.available-b.available);
}

export function flightRadioIntervals(frames,a,b,start,end) {
    const pair=[],network=[];
    const append=(list,left,right,state)=>{
        const previous=list.at(-1);
        if(previous&&previous.end===left&&previous.state===state)previous.end=right;
        else list.push({start:left,end:right,state});
    };
    for(const frame of frames){
        const left=Math.max(start,frame.interval_start_s),right=Math.min(end,frame.interval_end_s);
        if(!finite(left)||!finite(right)||right<=left)continue;
        const link=(frame.links||[]).find(link=>samePair(link,a,b));
        const state=link?.available===true||link?.available===1 ? true : link?.available===false||link?.available===0 ? false : null;
        append(pair,left,right,state);
        if(frame.metrics?.search_connected===0||frame.metrics?.search_connected===false)append(network,left,right,false);
    }
    return {pair,network};
}

function normalizeResponse(data,observer,peer) {
    const rows=Array.isArray(data)?data:[...(data?.observations||[]),...(data?.at?.observations||[])];
    const unique=new Map();
    for(const row of rows){
        if(row.observer_id!==observer||row.peer_id!==peer||!finite(timeOf(row)))continue;
        unique.set(timeOf(row),row);
    }
    return [...unique.values()].sort((a,b)=>timeOf(a)-timeOf(b));
}

/**
 * node is the receiver and peer the sender, matching the dashboard observation API.
 * fetchObservations({runId,revision,observer,peer,from,to,at}) returns the raw timeline
 * response or its observations array. Only saved logs are requested here.
 */
export function createFlightRadio(host,{Plot,seek,fetchObservations,signalTemplate}) {
    let state=null,disposed=false,sequence=0,activeKey='',loadedKey='',error='',loading=false;
    let sources=[[],[]],samples=[[],[]],sampleMetric='',sampleSources=null;
    let metric=fields[0].key,working=null,dirty=false,fullRedraw=true,plotted=false;
    let lastWindow='',lastOpacities=[],pointClicked=false;
    const cache=new Map();
    const heading=element('div','flight-radio-heading');
    const pairHeading=element('div','flight-radio-pair-heading');
    const title=element('h4','flight-radio-title','선택한 두 드론의 직접 링크');
    const pairStatus=element('strong','flight-radio-pair-status');
    const via=element('span','flight-radio-detour');
    pairHeading.append(title,pairStatus,via);
    const selectorLabel=element('label','flight-radio-select-label','표시할 수치');
    const selector=element('select','flight-radio-metric');selector.id='flightRadioMetric';
    for(const field of fields.filter(field=>field.chart))selector.add(new Option(field.label+' · '+field.unit,field.key));
    selectorLabel.append(selector);heading.append(pairHeading,selectorLabel);
    const readings=element('div','flight-radio-readings');
    const readout=[0,1].map(i=>{
        const box=element('div','flight-radio-reading');box.dataset.direction=i;
        const name=element('strong','flight-radio-direction');
        const value=element('strong','flight-radio-value','—');
        const age=element('span','flight-radio-age','기록을 불러오는 중');
        const measured=element('span','flight-radio-measured');
        box.append(name,value,age,measured);readings.append(box);
        return {box,name,value,age,measured};
    });
    const message=element('p','flight-radio-message');message.setAttribute('role','status');
    const plot=element('div','flight-radio-plot');plot.id='flightRadioPlot';
    plot.setAttribute('aria-label','선택한 두 드론의 양방향 실제 수신 측정과 직접 링크 연결 기록');
    const legend=element('div','flight-radio-legend');
    for(const [kind,label] of [['network','수색망 단절'],['up','두 드론 직접 연결'],['down','두 드론 직접 단절']]){
        const item=element('span','flight-radio-legend-item',label);item.dataset.kind=kind;legend.append(item);
    }
    const hint=element('p','flight-radio-hint','점은 실제 수신 측정이며 빈 구간은 새 측정이 없는 구간입니다. 흐린 점은 현재 재생 시각 이후의 저장 기록입니다. 점을 누르면 해당 관측이 공개된 시각으로 이동합니다.');
    const distinction=element('p','flight-radio-hint','두 드론의 직접 링크가 끊겨도 다른 드론을 거쳐 수색망은 연결될 수 있습니다. 높은 신호 값 하나만으로 연결·단절 원인을 판정하지 않습니다.');
    const details=element('details','flight-radio-details');
    details.append(element('summary','','무선 관측 5개 항목 보기'));
    const table=element('table','flight-radio-table');
    const thead=element('thead'),headRow=element('tr');
    headRow.append(element('th','','관측 항목'));
    const directionHeads=[element('th'),element('th')];headRow.append(...directionHeads);thead.append(headRow);
    const tbody=element('tbody');
    const detailValues=fields.map(field=>{
        const tr=element('tr');tr.dataset.rfField=field.key;
        tr.append(element('th','',field.label));
        const cells=[element('td'),element('td')];tr.append(...cells);tbody.append(tr);
        return cells;
    });
    table.append(thead,tbody);
    const tableWrap=element('div','flight-radio-table-wrap');tableWrap.append(table);details.append(tableWrap);
    details.append(element('p','flight-radio-hint','비콘은 주기적으로 보내는 신호, 응답(ACK)은 패킷을 받았다는 신호입니다. 표의 화살표는 비콘·ACK 수신 방향입니다. 구간 응답 성공률은 반대 방향으로 데이터를 보낸 뒤 ACK를 받은 비율이며, 비행 전체 PDR과 다릅니다. 전송 시도가 없는 구간은 미측정입니다. 미측정·유효하지 않은 값은 0으로 대체하지 않습니다.'));
    host.classList.add('flight-radio');host.replaceChildren(heading,readings,message,plot,legend,hint,distinction,details);

    function pairNames() {
        const fleet=state?.manifest?.fleet||[];
        const name=id=>droneMapLabel(id,fleet);
        return [name(state.peer)+' → '+name(state.node),name(state.node)+' → '+name(state.peer)];
    }
    function duration() {
        const manifest=state?.manifest||{};
        const value=manifest.simulation_end_s ?? manifest.duration_s ?? manifest.final_metrics?.duration_s;
        if(finite(value))return value;
        return state?.overview?.rf_frames?.at(-1)?.interval_end_s ?? state?.window?.[1] ?? 0;
    }
    function pairIdentity(next) {
        return JSON.stringify([next.runId,next.revision,next.node,next.peer]);
    }
    function displayReading(reading,field) {
        if(!reading.valid)return reading.reason;
        return fmt(field.unit==='%'?reading.value*100:reading.value,1)+' '+field.unit;
    }
    function showReadings() {
        if(!state)return;
        const names=pairNames(),field=fields.find(field=>field.key===metric);
        names.forEach((name,i)=>{
            directionHeads[i].textContent=name;
            const reading=flightRadioReading(sources[i],metric,state.time);
            const target=readout[i];target.name.textContent=name;
            target.value.textContent=loading?'—':displayReading(reading,field);
            target.box.dataset.valid=String(!loading&&reading.valid);
            target.age.textContent=loading?'양방향 기록을 불러오는 중':reading.valid?(finite(reading.age)?fmt(reading.age,2)+'초 전 측정':'측정 시차 미기록'):'';
            target.measured.textContent=!loading&&reading.valid&&finite(reading.measured)?'측정 '+fmt(reading.measured,2)+'초 · 기록 '+fmt(reading.available,1)+'초':'';
        });
        fields.forEach((field,index)=>{
            detailValues[index].forEach((cell,i)=>{
                const reading=flightRadioReading(sources[i],field.key,state.time);
                cell.replaceChildren(element('span','',loading?'불러오는 중':displayReading(reading,field)));
                if(!loading&&reading.valid&&field.chart){
                    cell.append(element('small','',finite(reading.age)?fmt(reading.age,2)+'초 전 측정':'측정 시차 미기록'));
                }
            });
        });
        const frame=intervalAt(state.overview?.rf_frames||[],state.time);
        const link=frame?.links?.find(link=>samePair(link,state.node,state.peer));
        const directUp=link?.available===true||link?.available===1;
        const directDown=link?.available===false||link?.available===0;
        const name=id=>droneMapLabel(id,state.manifest?.fleet||[]);
        const pairState=directUp?'직접 연결':directDown?'직접 링크 단절':'직접 연결 상태 미기록';
        pairStatus.textContent=name(state.node)+' ↔ '+name(state.peer)+' · '+pairState;
        pairStatus.dataset.down=String(directDown);
        via.textContent=directDown?detourText(state.route??null,name):'';
        message.textContent=loading?'선택한 드론 쌍의 저장 기록을 불러오는 중입니다.':error?'관측을 불러오지 못했습니다. '+error
            :fmt(state.time,1)+'초까지 공개된 최근 관측입니다.';
        host.dataset.loadState=loading?'loading':error?'error':'ready';
    }
    async function loadPair() {
        const ticket=++sequence,key=activeKey;
        const {runId,revision,node,peer}=state;
        sources=[[],[]];loadedKey='';loading=true;error='';fullRedraw=true;
        plot.style.visibility='hidden';showReadings();
        const cacheKey=JSON.stringify([runId,revision,[node,peer].sort()]);
        try {
            let byDirection=cache.get(cacheKey);
            if(!byDirection){
                const end=duration();
                const queries=[{observer:node,peer},{observer:peer,peer:node}];
                const results=await Promise.all(queries.map(query=>fetchObservations({runId,revision,...query,from:0,to:end,at:state.time})));
                byDirection=new Map(queries.map((query,i)=>[query.observer+'|'+query.peer,normalizeResponse(results[i],query.observer,query.peer)]));
                if(disposed||ticket!==sequence||key!==activeKey)return;
                cache.set(cacheKey,byDirection);
                while(cache.size>2)cache.delete(cache.keys().next().value);
            }
            if(disposed||ticket!==sequence||key!==activeKey)return;
            sources=[byDirection.get(node+'|'+peer)||[],byDirection.get(peer+'|'+node)||[]];
            loadedKey=key;sampleSources=null;loading=false;fullRedraw=true;
            showReadings();draw();
        }catch(cause){
            if(disposed||ticket!==sequence||key!==activeKey)return;
            sources=[[],[]];loading=false;error=cause?.message||'알 수 없는 오류';
            loadedKey=key;sampleSources=null;fullRedraw=true;showReadings();draw();
        }
    }
    function bindSeeking() {
        plot.on('plotly_click',event=>{
            const available=event.points?.[0]?.customdata?.[0];
            if(disposed||!finite(available))return;
            pointClicked=true;seek(available);
            setTimeout(()=>{pointClicked=false;},75);
        });
        plot.addEventListener('click',event=>{
            const axis=plot._fullLayout?.xaxis;
            if(disposed||!axis||!event.target.closest('.nsewdrag,.scatterlayer,.draglayer'))return;
            const x=event.clientX-plot.getBoundingClientRect().left-axis._offset;
            if(x<0||x>axis._length)return;
            const t=axis.p2d(x);
            setTimeout(()=>{if(!disposed&&!pointClicked&&plot.isConnected)seek(t);},0);
        });
    }
    function figure() {
        if(sampleSources!==sources||sampleMetric!==metric){
            samples=sources.map(rows=>flightRadioSamples(rows,metric));sampleSources=sources;sampleMetric=metric;
        }
        const field=fields.find(field=>field.key===metric),names=pairNames();
        const end=duration(),window=state.window||[Math.max(0,state.time-15),Math.min(end,state.time+15)];
        const traces=samples.map((points,i)=>{
            const visible=points.filter(point=>point.measured>=window[0]&&point.measured<=window[1]);
            return {...structuredClone(signalTemplate?.data?.[0]||{}),type:'scatter',mode:'markers',name:names[i],showlegend:false,connectgaps:false,
                x:visible.map(point=>point.measured),y:visible.map(point=>point.value),
                customdata:visible.map(point=>[point.available,point.available>state.time?'이후 저장 기록':'현재까지 공개됨']),
                marker:{size:7,color:colors[i],symbol:i?'diamond':'circle',opacity:visible.map(point=>point.available>state.time ? .25 : 1),line:{color:'white',width:.6}},
                hovertemplate:names[i]+' · %{y:.2f} '+field.unit+'<br>실제 측정 %{x:.3f}초<br>공개 기록 %{customdata[0]:.1f}초 · %{customdata[1]}<extra></extra>'};
        });
        const values=traces.flatMap(trace=>trace.y),min=values.length?Math.min(...values):field.unit==='dB'?0:-100,max=values.length?Math.max(...values):field.unit==='dB'?20:-60;
        const pad=Math.max(2,(max-min)*.09);
        const layout=structuredClone(signalTemplate?.layout||{});
        layout.height=252;layout.margin={l:52,r:18,t:12,b:48};layout.showlegend=false;
        layout.paper_bgcolor='#fff';layout.plot_bgcolor='#f8fbfd';
        layout.font={...(layout.font||{}),family:'Arial, 맑은 고딕, sans-serif',size:12,color:'#304b60'};
        // The pair-state strip occupies paper y=.045–.115, below the signal
        // axis domain. Keep time ticks and their title beneath that strip.
        layout.xaxis={...layout.xaxis,anchor:'free',position:0,range:[...window],title:{text:'비행 시각 (초)',standoff:6},nticks:7,fixedrange:true,gridcolor:'#e2e9ef',zeroline:false};
        layout.yaxis={...layout.yaxis,range:[min-pad,max+pad],title:{text:field.unit,standoff:6},domain:[.18,1],nticks:4,fixedrange:true,gridcolor:'#e2e9ef',zeroline:false};
        const intervals=flightRadioIntervals(state.overview?.rf_frames||[],state.node,state.peer,...window);
        layout.shapes=[
            {type:'line',xref:'x',yref:'paper',x0:state.time,x1:state.time,y0:0,y1:1,line:{color:'#213d51',width:1.6,dash:'dot'}},
            ...intervals.network.map(interval=>({type:'rect',xref:'x',yref:'paper',x0:interval.start,x1:interval.end,y0:.18,y1:1,layer:'below',fillcolor:'#f7e9d8',line:{width:0}})),
            ...intervals.pair.map(interval=>({type:'rect',xref:'x',yref:'paper',x0:interval.start,x1:interval.end,y0:.045,y1:.115,layer:'below',fillcolor:interval.state===null?'#dfe5ea':interval.state?'#79b2a5':'#bc504a',line:{width:0}}))
        ];
        layout.annotations=values.length?[]:[{text:'이 시간창에 유효한 실제 측정점이 없습니다',x:.5,y:.6,xref:'paper',yref:'paper',showarrow:false,font:{color:'#697d8e',size:13}}];
        return {traces,layout,windowKey:JSON.stringify(window)};
    }
    async function render() {
        while(dirty&&!disposed){
            dirty=false;
            if(!state||loading||loadedKey!==activeKey)continue;
            showReadings();
            if(!Plot){message.textContent='통신 수치는 확인할 수 있지만 그래프 도구를 불러오지 못했습니다.';continue;}
            const key=activeKey,currentSequence=sequence,view=figure();
            if(fullRedraw||!plotted||lastWindow!==view.windowKey){
                fullRedraw=false;
                if(plotted)await Plot.react(plot,view.traces,view.layout,plotOptions);
                else {await Plot.newPlot(plot,view.traces,view.layout,plotOptions);plotted=true;if(!disposed)bindSeeking();}
                lastWindow=view.windowKey;
            }else{
                const change={'shapes[0].x0':state.time,'shapes[0].x1':state.time};
                await Plot.relayout(plot,change);
                const opacities=view.traces.map(trace=>trace.marker.opacity);
                if(JSON.stringify(opacities)!==JSON.stringify(lastOpacities)){
                    await Plot.restyle(plot,{'marker.opacity':opacities,customdata:view.traces.map(trace=>trace.customdata)});
                }
            }
            lastOpacities=view.traces.map(trace=>trace.marker.opacity);
            if(disposed){if(plotted)Plot.purge(plot);return;}
            if(key===activeKey&&currentSequence===sequence)plot.style.visibility='';
        }
    }
    function draw() {
        if(disposed)return Promise.resolve();
        dirty=true;
        if(!working)working=render().catch(cause=>{
            if(!disposed){error=cause?.message||'그래프 표시 오류';showReadings();}
        }).finally(()=>{working=null;if(dirty&&!disposed)draw();});
        return working;
    }
    selector.onchange=()=>{metric=selector.value;fullRedraw=true;draw();};
    return {
        setState(next){
            if(disposed)return;
            const previousFrames=state?.overview?.rf_frames;
            state={...next,window:next.window?.slice()};
            if(!next.runId||!next.node||!next.peer||next.node===next.peer){
                ++sequence;activeKey='';loadedKey='';sources=[[],[]];loading=false;error='';
                pairStatus.textContent='';via.textContent='';
                message.textContent='지도 옆에서 서로 다른 두 드론을 선택하세요.';plot.style.visibility='hidden';host.dataset.loadState='empty';
                directionHeads.forEach(head=>{head.textContent='미선택';});
                detailValues.forEach(cells=>cells.forEach(cell=>{cell.textContent='미선택';}));
                readout.forEach(row=>{row.name.textContent='미선택';row.value.textContent='—';row.age.textContent='';row.measured.textContent='';row.box.dataset.valid='false';});return;
            }
            const key=pairIdentity(next);
            if(key!==activeKey){activeKey=key;loadPair();}
            else {if(previousFrames!==next.overview?.rf_frames)fullRedraw=true;showReadings();draw();}
        },
        dispose(){
            disposed=true;++sequence;cache.clear();selector.onchange=null;
            if(plotted&&!working)Plot?.purge(plot);
            host.replaceChildren();host.classList.remove('flight-radio');
        }
    };
}
