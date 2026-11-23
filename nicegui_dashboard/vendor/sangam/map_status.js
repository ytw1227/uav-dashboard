import {warningThresholds} from './margin_data.js';

// Shared by WebGL and canvas so the same saved state has the same map color.
const colors = {risk:'#ffd44f', down:'#ff6677', unknown:'#9baaba'};
const connected = {search:'#45d9c8', relay:'#a3afff'};

export function aircraftPaint(communication, id) {
    const node = communication?.nodes?.find(row => row.id === id);
    const color = node?.status === 'connected' ? connected[node.role] : colors[node?.status];
    const text = node?.text || '상태 미확인';
    const parts=[text,node?.detail];
    if(node?.status==='risk')parts.push('표시 기준 '+warningThresholds.enter+' dB 이하 · 해제 '+warningThresholds.exit+' dB 이상');
    const description=parts.filter(Boolean).join(' · ');
    return {color:color || colors.unknown, text, description, label:node?.label, status:node?.status};
}

export function selectedReadout(element, communication, id) {
    if (!element) return;
    element.hidden = !id;
    if (!id) return;
    const marker = aircraftPaint(communication,id);
    const caption = '선택 기체 ' + (marker.label || id) + ' · ' + marker.description;
    if (element.textContent !== caption) element.textContent = caption;
    element.style.setProperty('--state-color',marker.color);
}
