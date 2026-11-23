import {intervalAt} from './render.js';
import {recoveryRoutes} from './incident_radio.js';

const state=(frame)=>frame?.metrics?.search_connected;
const down=frame=>state(frame)===false||state(frame)===0;
const up=frame=>state(frame)===true||state(frame)===1;
const pairKey=(a,b)=>[a,b].sort().join('|');

// The native persistent catalog excludes half-second outages. Derive their geometry
// using the same recorded edge availability and before/onset/end frames.
function briefCase(first,last,frames,fleet){
    const start=first.interval_start_s, end=last.interval_end_s;
    const before=intervalAt(frames,start-.5),after=intervalAt(frames,end);
    const searches=new Set(fleet.filter(n=>n.role==='search').map(n=>n.id));
    const groups=first.components.map(group=>group.filter(id=>searches.has(id))).filter(group=>group.length);
    const component=new Map();
    first.components.forEach((group,index)=>group.forEach(id=>component.set(id,index)));
    const active=new Set(first.links.filter(link=>link.available).map(l=>pairKey(l.source,l.target)));
    const lost=(before?.links||[]).filter(l=>l.available&&!active.has(pairKey(l.source,l.target)))
        .map(link=>[link.source,link.target]);
    const event={
        id:'brief-'+start, brief:true, start_s:start, end_s:end, duration_s:end-start,
        left_censored:start===0, right_censored:end===frames.at(-1).interval_end_s,
        recovery_s:up(after)?end:null,
        window:[Math.max(0,start-15),Math.min(frames.at(-1).interval_end_s,end+15)],
        components_at_start:first.components, search_groups:groups, lost_links:lost,
        crossing_lost_links:lost.filter(([a,b])=>component.get(a)!==component.get(b)),
        decisions:[], interventions:[], prior_interventions:[], window_outages:[]
    };
    return event;
}

export function makeIncidentTimeline(c){
    const selected=c.event, frames=c.frames||c.run.overview.rf_frames, fleet=c.run.manifest.fleet;
    const [left,right]=selected.window;
    const byStart=new Map(c.book.events.map(event=>[event.start_s,event]));
    const timeline=[];
    let first=null, last=null;
    function finish(){
        if(!first)return;
        const start=first.interval_start_s,end=last.interval_end_s;
        if(start<=right&&end>=left){
            const e=byStart.get(start)||briefCase(first,last,frames,fleet);
            const relation=e.id===selected.id?'selected':start<selected.start_s?'previous':'next';
            const routes=relation==='selected'&&c.routes?c.routes:recoveryRoutes(e,frames,fleet);
            timeline.push({event:e,analysis:c.analyses?.[e.id]||(relation==='selected'?c.analysis:null),routes,relation});
        }
        first=last=null;
    }
    for(const frame of frames){
        if(frame.interval_start_s>right&&!first)break;
        if(!down(frame)){finish();continue;}
        if(last&&last.interval_end_s!==frame.interval_start_s)finish();
        if(!first)first=frame;
        last=frame;
    }
    finish();
    const original=timeline.find(row=>row.relation==='selected')||
        {event:selected,analysis:c.analysis,routes:c.routes,relation:'selected'};
    return function at(t){
        const latest=timeline.findLast(row=>row.event.start_s<=t);
        if(!latest||latest.relation==='selected')return original;
        const e=latest.event;
        if(t<e.end_s||e.right_censored||latest.relation==='next')return latest;
        // Show a preceding event's recovery near its confirmed boundary or control
        // outcome. Then resume the selected episode's pre-outage observations.
        const lastOutcome=Math.max(e.end_s,...e.interventions.map(a=>a.outcome_available_s).filter(Number.isFinite));
        if(t<lastOutcome+3)return latest;
        return original;
    };
}
