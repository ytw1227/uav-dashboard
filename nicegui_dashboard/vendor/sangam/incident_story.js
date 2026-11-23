import {displayDrone} from './incident_map.js';

const patterns={abrupt:'급락형',gradual:'점진형',mixed:'혼합형',other:'기타',insufficient_history:'이력 부족'};
const factors={building_change:'건물 차폐',distance_change:'거리 감쇠',interference_change:'수신점 간섭',shadow_change:'섀도잉'};
const summary=(value,unit)=>Number.isFinite(value)?Number(value.toFixed(2))+' '+unit:'미기록';

function node(tag, text, cls){
    const el=document.createElement(tag);
    if(text!==undefined)el.textContent=text;
    if(cls)el.className=cls;
    return el;
}
function setLabel(ids, fleet){
    return ids.map(id=>displayDrone(id,fleet)).join(' · ');
}

export function renderDiagnosis(host,c){
    const a=c.analysis, e=c.event, fleet=c.run.manifest.fleet;
    const heading=node('h3','단절 원인과 유형');
    const scope=node('span','선택 사건 · '+e.start_s+'초','diagnosis-scope');
    scope.id='incidentStoryScope';
    heading.append(scope);
    host.replaceChildren(heading);
    if(!a){host.append(node('p','이 실행의 원인 분석 기록이 없습니다.'));return;}
    const factorsText=a.factors.map(group=>group.map(k=>factors[k]||k).join(' + '));
    const causeTitle=factorsText.length>1?'여러 원인 조합 가능':factorsText[0]||'판정 보류';
    const title=node('div',undefined,'analysis-title');
    title.append(node('strong',patterns[a.pattern]||a.pattern,'pattern-name'),node('span',causeTitle,'factor-name'));
    host.append(title);
    if(Number.isFinite(a.margin_before_db)&&Number.isFinite(a.margin_at_onset_db)){
        host.append(node('p','단절 직전 0.5초 · 사건 분석 여유(근사) '+summary(a.margin_before_db,'dB')+' → '+summary(a.margin_at_onset_db,'dB'),'diagnosis-margin'));
    }
    const summaryText=a.isolated.length
        ?setLabel(a.isolated,fleet)+' 고립'
        :'수색망 '+a.groups.length+'개 그룹으로 분리';
    const linkNames=a.edges.map(({pair})=>pair.map(id=>displayDrone(id,fleet)).join('–')).join(', ');
    host.append(node('p',summaryText+(linkNames?' · 끊긴 링크 '+linkNames:''),'diagnosis-topology'));
    if(factorsText.length){
        let reason;
        if(factorsText.length>1)reason='직전 값으로 되돌려 연결이 회복된 조합은 '+factorsText.join(' 또는 ')+'입니다.';
        else if(a.factors[0].length>1)reason=factorsText[0]+'를 함께 직전 값으로 되돌렸을 때 연결이 회복됐습니다.';
        else reason=causeTitle+'만 직전 값으로 되돌리면 단절 시작 시점에 수색망이 연결됩니다.';
        host.append(node('p',reason,'diagnosis-finding'));
    }
    const details=node('details',undefined,'diagnosis-details');
    details.append(node('summary','수치와 판정 근거'));
    const delta=Number.isFinite(a.jump_db)?Math.abs(a.jump_db).toFixed(2)+' dB '+(a.jump_db>=0?'하락':'상승'):'변화량 미기록';
    details.append(node('p','단절 직전 0.5초의 사건 분석 여유 변화(근사) · '+delta));
    const shapeNotes={abrupt:'마지막 0.5초에 6 dB 이상 떨어졌습니다.',gradual:'마지막 반초 이전 두 구간의 통신 여유 중앙값이 3 dB 이상 떨어졌습니다.',mixed:'직전 6초에 점진적인 하락과 마지막 반초의 급락이 모두 있었던 사건입니다.',other:'급락과 점진 하락 기준 중 어느 쪽에도 해당하지 않습니다.',insufficient_history:'판정에 필요한 직전 6초 기록이 부족합니다.'};
    details.append(node('p',shapeNotes[a.pattern]));
    for(const edge of a.edges){
        const row=node('div',undefined,'edge-proof');
        row.append(node('strong',edge.pair.map(id=>displayDrone(id,fleet)).join(' ↔ ')));
        const cols=[['building_change','건물 추가손실',edge.building_db,'dB'],['distance_change','기체 간 거리',edge.distance_m,'m'],['shadow_change','섀도잉 손실',edge.shadow_db,'dB']];
        for(const [key,label,values,unit] of cols){
            if(a.factors.some(group=>group.includes(key))&&values)row.append(node('div',label+' '+summary(values[0],unit)+' → '+summary(values[1],unit)));
        }
        if(a.factors.some(group=>group.includes('interference_change'))&&edge.noise_dbm){
            edge.pair.forEach((id,i)=>row.append(node('div',displayDrone(id,fleet)+' 수신 간섭·잡음 '+summary(edge.noise_dbm[0][i],'dBm')+' → '+summary(edge.noise_dbm[1][i],'dBm'))));
        }
        row.append(node('small','낮은 방향의 전송 성공 확률 '+summary(100*edge.min_hop_probability[0],'%')+' → '+summary(100*edge.min_hop_probability[1],'%')+' · 양방향 기준 90%'));
        details.append(row);
    }
    details.append(node('p',e.start_s+'초 전후의 링크를 원본과 대조한 사후 전파 분석입니다. 아래 신호 그래프의 점은 각 방향에서 수신한 비콘 측정값입니다.'));
    host.append(details);
}
