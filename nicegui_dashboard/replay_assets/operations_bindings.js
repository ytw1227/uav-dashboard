// Add recorded duties beside communication readouts. Only connected relays get
// a duty shade; warning/separation states and all research definitions stay intact.
import {operationRoleColor, OPERATION_ROLE_COLORS} from './operations.js';

export function createOperationsBindings(operations) {
    let state=null, incident=null, disposed=false, pending=false;
    const roots=new Map();
    const byId=id=>document.getElementById(id);
    const observedIds=['nodeDetail','fmNodeStates','bmSelectedStatus'];
    const outputs=[];
    function outputAfter(anchor,id) {
        let node=byId(id);
        if(!node && anchor) {
            node=document.createElement('div');node.id=id;node.className='ops-map-readout';node.hidden=true;
            anchor.after(node);outputs.push(node);
        }
        return node;
    }
    function text(node,value) {
        if(!node)return;
        node.hidden=!value;
        if(node.textContent!==value)node.textContent=value;
    }
    function knownRole(id) {return id ? operations.getRole?.(id) : null;}
    function roleText(id) {return knownRole(id)?.label || '';}
    function paint(communication,id,fallback) {
        if (!state) return fallback;
        const node=communication?.nodes?.find(item=>item.id===id);
        if (node?.role !== 'relay') return fallback;
        return operationRoleColor(knownRole(id),node.status,fallback);
    }
    function nodeLabel(id) {
        const fleet=state?.manifest?.fleet || [];
        const item=fleet.find(n=>n.id===id);
        const index=fleet.filter(n=>n.role===item?.role).findIndex(n=>n.id===id);
        return item && index>=0 ? (item.role==='search'?'S':'R')+(index+1) : id || '';
    }
    function readout(time,id) {
        const read=operations.getReadout?.(time,id);
        const role=roleText(id);
        if(!read || !role)return '';
        const height=read.selected?.height;
        const heightLabel=read.selected?.heightInterpolated?'현재 고도(보간)':'기록 고도';
        return [nodeLabel(id), role, Number.isFinite(height)?`${heightLabel} ${height.toFixed(1)} m`:''].filter(Boolean).join(' · ');
    }
    function attachObservers() {
        for(const id of observedIds) {
            const node=byId(id);
            if(roots.get(id)?.node===node)continue;
            roots.get(id)?.observer.disconnect();roots.delete(id);
            if(!node)continue;
            const observer=new MutationObserver(schedule);
            observer.observe(node,{childList:true,subtree:true,characterData:true,attributes:id==='bmSelectedStatus',attributeFilter:id==='bmSelectedStatus'?['data-drone-id','hidden']:undefined});
            roots.set(id,{node,observer});
        }
    }
    function render() {
        pending=false;if(disposed)return;
        attachObservers();
        for(const card of byId('fmNodeStates')?.querySelectorAll('[data-inspect]') || []) {
            const recordedRole=state ? knownRole(card.dataset.inspect) : null;
            const value=recordedRole?.label || '';
            if(recordedRole && OPERATION_ROLE_COLORS[recordedRole.duty]) {
                card.dataset.operationsDuty=recordedRole.duty;
                card.style.setProperty('--ops-role-color',OPERATION_ROLE_COLORS[recordedRole.duty]);
            } else {
                delete card.dataset.operationsDuty;
                card.style.removeProperty('--ops-role-color');
            }
            let badge=card.querySelector('[data-operations-role]');
            if(!value){badge?.remove();continue;}
            if(!badge){badge=document.createElement('span');badge.dataset.operationsRole='';badge.className='ops-role-badge';card.append(badge);}
            text(badge,value);
        }
        const inspector=byId('nodeDetail');
        const role=state ? roleText(state.node) : '';
        let duty=inspector?.querySelector('[data-operations-duty]');
        if(role && inspector){
            if(!duty){duty=document.createElement('div');duty.dataset.operationsDuty='';duty.className='ops-duty-readout';inspector.append(duty);}
            text(duty,'이번 비행의 역할 · '+role);
        }else duty?.remove();
        const main=byId('bmSelectedStatus');
        text(outputAfter(main,'opsMainSelectedRole'),state && !main?.hidden ? readout(state.time,main?.dataset.droneId || state.node) : '');
        renderLegend(byId('mapPanel')?.querySelector('.map-legend'));
        renderLegend(byId('incidentDialog')?.querySelector('.incident-comm-key'));
        renderIncident();
    }
    function renderLegend(legend) {
        if (!legend) return;
        const special=state ? (state.manifest?.fleet || []).map(item=>knownRole(item.id))
            .filter(role=>role && OPERATION_ROLE_COLORS[role.duty]) : [];
        const generic=legend.querySelector('.relay-up')?.closest('.map-role');
        if (generic && !generic.dataset.operationsOriginal) generic.dataset.operationsOriginal=generic.innerHTML;
        if (generic) {
            const content=special.length ? '<i class="comm-dot relay-up" aria-hidden="true"></i>일반 중계 · 연결' : generic.dataset.operationsOriginal;
            if (generic.innerHTML!==content) generic.innerHTML=content;
        }
        const expected=new Set(special.map(role=>role.duty));
        for (const badge of legend.querySelectorAll('[data-operations-legend]')) if(!expected.has(badge.dataset.operationsLegend))badge.remove();
        let anchor=generic;
        for (const role of special.sort((a,b)=>a.duty.localeCompare(b.duty))) {
            let badge=legend.querySelector('[data-operations-legend="'+role.duty+'"]');
            if(!badge){
                badge=document.createElement('span');badge.className='map-role ops-role-legend';badge.dataset.operationsLegend=role.duty;
                badge.title='연결 상태에서의 역할 색입니다. 연결 여유가 낮으면 노랑, 과반 그룹과 분리되면 빨강을 우선합니다.';
                const dot=document.createElement('i');dot.className='comm-dot';dot.setAttribute('aria-hidden','true');
                dot.style.background=OPERATION_ROLE_COLORS[role.duty];badge.append(dot,document.createTextNode(''));
                if(anchor)anchor.after(badge);else legend.append(badge);
            }
            const caption=(role.duty==='fixed_relay'?'고정 중계 ':'고도 중계 ')+nodeLabel(role.drone_id)+' · 연결';
            if(badge.lastChild.textContent!==caption)badge.lastChild.textContent=caption;
            anchor=badge;
        }
    }
    function renderIncident() {
        const host=outputAfter(byId('incidentSelectedStatus'),'opsIncidentReadout');
        if(!incident || !state || !byId('incidentDialog')?.open ||
            incident.runId!==state.manifest?.run_id || incident.revision!==state.manifest?.bundle_revision){text(host,'');return;}
        const selection=byId('incidentSelectedStatus');
        const selected=(!selection?.hidden && selection?.dataset.droneId) || incident.observer;
        const altitudeId=(state.manifest?.fleet || []).find(n=>knownRole(n.id)?.duty==='altitude_relay')?.id;
        const id=selected || altitudeId;
        const value=operations.getReadout?.(incident.time,id);
        const caption=readout(incident.time,id);
        text(host,caption && value ? (selected?'':'고도 담당 · ')+caption+
            (value.stage?.droneId===id && value.stage?.label?' · '+value.stage.label:'') : '');
    }
    function schedule() {
        if(pending||disposed)return;pending=true;queueMicrotask(render);
    }
    return {
        paint,
        update(s){state=s;render();},
        refresh:render,
        updateIncident({context,time}){
            incident={time,runId:context.run?.manifest?.run_id,
                revision:context.run?.manifest?.bundle_revision,observer:context.trackedId};renderIncident();
        },
        reset(){state=null;incident=null;render();},
        dispose(){disposed=true;for(const {observer} of roots.values())observer.disconnect();roots.clear();for(const node of outputs)node.remove();},
    };
}
