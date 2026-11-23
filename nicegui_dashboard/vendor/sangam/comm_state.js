import {marginRecords, warningThresholds} from './margin_data.js';

const noSeries = Object.freeze({times: [], values: []});
const marginSource = '저장된 비행을 0.5초 구간별로 분석한 값입니다. 링크마다 두 방향의 신호 품질을 같은 dB만큼 바꿔, 양방향 성공률이 모두 90% 이상이 되는 경계까지의 여유를 구합니다. 각 수색 쌍에서 가장 약한 링크의 여유가 최대인 경로를 찾고, 세 수색 쌍 가운데 가장 작은 값을 표시합니다. 0 dB는 기준 충족입니다.';
const finite = Number.isFinite;
const graphCache = new WeakMap();

export function formatMargin(value) {
    if (!finite(value)) return '산출 불가';
    if (value === 0) return '0 dB';
    const abs = Math.abs(value);
    const number = abs < .05 ? abs.toPrecision(2) : abs.toFixed(1);
    return (value > 0 ? '+' : '−') + number + ' dB';
}

function lastAt(rows, time, stamp) {
    let start = 0, end = rows.length;
    while (start < end) {
        const mid = Math.floor((start + end) / 2);
        if (stamp(rows[mid]) <= time) start = mid + 1;
        else end = mid;
    }
    return start - 1;
}

function recordedRisk(run, time) {
    const manifest = run?.manifest;
    const empty = {label: '미평가', detail: '이 판단 구간의 편대 위험 점수 기록 없음', time: null, score: null, threshold: null,
        probability: null, probabilityText: '확률 미산출', probabilityDetail: '이 판단 구간에는 편대 분류 모델의 출력이 없습니다.'};
    if (manifest?.mode === 'nominal') return {...empty, label: '미적용', detail: '기본 대형 비행', probabilityDetail: '기본 대형 비행에서는 편대 예측 모델을 사용하지 않습니다.'};
    const rows = run?.overview?.decisions || [];
    const row = rows[lastAt(rows, time, row => row.decision_time_s)];
    const dt = manifest?.clocks?.decision_dt_s;
    if (!row || !finite(dt) || time >= row.decision_time_s + dt || time >= manifest?.simulation_end_s) return empty;
    const trace = row.control_trace;
    const when = row.decision_time_s;
    const risk = {...empty, time: when};
    if (finite(trace?.early_score) && finite(trace?.threshold)) {
        risk.label = trace.early_score >= trace.threshold ? '주의' : '낮음';
        risk.detail = '예방 판단은 편대 예측 점수 75%와 기존 예방 점수 25%를 합친 값으로 결정합니다.';
        risk.score = trace.early_score;
        risk.threshold = trace.threshold;
    }
    // FleetForecaster.early_score adds the softmax masses of onset classes [3,5)
    // and [5,7). Training label_network excludes disconnected starts and the tail.
    if (trace?.score_scope !== 'v21_transformer_blended_prevention_gate' ||
        trace.score_status !== 'recorded' || !finite(trace.fleet_risk) ||
        trace.fleet_risk < 0 || trace.fleet_risk > 1) return risk;
    if (trace.actual_pre_action_connected !== true || when <= 0) {
        risk.probabilityDetail = '편대 모델의 새 단절 예측은 판단 직전 수색망이 연결된 경우에 해석할 수 있습니다.';
        return risk;
    }
    if (!finite(manifest.simulation_end_s) || when + 7 >= manifest.simulation_end_s) {
        risk.probabilityDetail = '비행 종료 전 7초 예측 구간이 온전히 확보되지 않았습니다.';
        return risk;
    }
    const percent = trace.fleet_risk * 100;
    let number = percent.toLocaleString('ko-KR', {maximumFractionDigits: percent < 1 || percent > 99 ? 2 : 1});
    if (percent > 0 && number === '0') number = '<0.01';
    if (percent < 100 && number === '100') number = '>99.99';
    risk.probability = trace.fleet_risk;
    risk.probabilityText = '모델 추정 ' + number + '%';
    risk.probabilityDetail = when + '초 판단에서 ' + (when + 3) + '초 이상 ' + (when + 7) +
        '초 미만에 다음 1초 이상 수색망 단절이 시작될 모델 추정 확률입니다. 기준 비행 기록으로 학습한 분류기의 출력이며 적용 비행에서의 실제 발생 빈도에 맞춘 확률 보정은 확인되지 않았습니다.';
    return risk;
}

function connectedGraph(frame, fleet) {
    if (!frame || !fleet.length) return null;
    if (graphCache.has(frame)) return graphCache.get(frame);
    const neighbors = new Map(fleet.map(n => [n.id, []]));
    const edges = new Set();
    for (const {source, target, available} of frame.links || []) {
        if (!neighbors.has(source) || !neighbors.has(target) || source === target || typeof available !== 'boolean') return null;
        const key = [source, target].sort().join('|');
        if (edges.has(key)) return null;
        edges.add(key);
        if (available) {
            neighbors.get(source).push(target);
            neighbors.get(target).push(source);
        }
    }
    if (edges.size !== fleet.length * (fleet.length - 1) / 2) return null;
    const seen = new Set(), groups = [];
    for (const {id} of fleet) {
        if (seen.has(id)) continue;
        const group = [id];
        seen.add(id);
        for (let i = 0; i < group.length; i++) {
            for (const next of neighbors.get(group[i])) {
                if (!seen.has(next)) { group.push(next); seen.add(next); }
            }
        }
        groups.push(group);
    }
    const searchIds = new Set(fleet.filter(node => node.role === 'search').map(node => node.id));
    const majorityGroup = groups.find(group => group.filter(id => searchIds.has(id)).length > searchIds.size / 2);
    const graph = {neighbors, groups, majorityGroup, hasSearch: searchIds.size > 0};
    graphCache.set(frame, graph);
    return graph;
}

function availableRoute(graph, a, b) {
    if (!graph?.neighbors.has(a) || !graph.neighbors.has(b)) return null;
    const parent = new Map([[a, null]]), queue = [a];
    for (let i = 0; i < queue.length; i++) {
        const node = queue[i];
        if (node === b) {
            const path = [];
            for (let id = b; id !== null; id = parent.get(id)) path.unshift(id);
            return path;
        }
        for (const next of graph.neighbors.get(node)) {
            if (!parent.has(next)) { parent.set(next, node); queue.push(next); }
        }
    }
    return [];
}

export function detourText(route, name) {
    if (!Array.isArray(route)) return '우회 경로 확인 불가';
    if (route.length === 0) return '우회 경로 없음';
    if (route.length <= 2) return '';
    return route.slice(1, -1).map(name).join(' → ') + ' 경유로 연결됨';
}

function describeNodes(fleet, graph, warning) {
    const counts = {};
    return fleet.map(({id, role}) => {
        counts[role] = (counts[role] || 0) + 1;
        const label = role === 'search' ? 'S' + counts[role] : role === 'relay' ? 'R' + counts[role] : id;
        const index = graph?.groups.findIndex(group => group.includes(id)) ?? -1;
        if (index < 0) return {id, label, role, status: 'unknown', text: '상태 확인 불가', detail: '이 시각의 연결 기록 없음', group: null};
        const members = graph.groups[index];
        if (!graph.hasSearch) return {id, label, role, status: 'unknown', text: '상태 확인 불가', detail: '기준이 될 수색기 없음', group: index + 1};
        if (members !== graph.majorityGroup) {
            const isolated = members.length === 1;
            const reason = graph.majorityGroup ? '수색기 과반 그룹과 분리' : '수색기 과반 그룹 없음';
            const detail = isolated ? '가용 링크 없음 · ' + reason : reason;
            return {id, label, role, status: 'down', text: isolated ? '고립' : '그룹 분리', detail, group: index + 1};
        }
        if (warning.active && warning.ids.includes(id)) {
            return {id, label, role, status: 'risk', text: '연결 · 통신 여유 부족', detail: warning.detail, group: index + 1};
        }
        const detail = warning.hasMargin ? '' : '통신 여유 미산출';
        return {id, label, role, status: 'connected', text: '연결', detail, group: index + 1};
    });
}

export function commAt(run, frame, time, a, b) {
    const manifest = run?.manifest;
    const fleet = manifest?.fleet || [];
    const id = run?.runId ?? run?.id ?? manifest?.run_id;
    const record = marginRecords[id];
    const matching = record && record.revision === (run?.revision ?? manifest?.bundle_revision);
    const marginSeries = matching ? record : noSeries;
    const valid = frame && frame.interval_start_s <= time && time < frame.interval_end_s;
    const current = valid ? frame : null;
    const flag = current?.metrics?.search_connected;
    const connected = flag === true || flag === 1 ? true : flag === false || flag === 0 ? false : null;
    const index = current ? lastAt(marginSeries.times, current.interval_start_s, x => x) : -1;
    const value = index >= 0 && marginSeries.times[index] === current.interval_start_s ? marginSeries.values[index] : null;
    const margin = finite(value) && connected !== null && (value >= 0) === connected ? value : null;
    const ids = path => (path || []).map(index => fleet.find(n => n.index === index)?.id ?? fleet[index]?.id).filter(Boolean);
    const graph = connectedGraph(current, fleet);
    const edge = margin === null ? [] : ids(record.bottleneckEdges?.[index]);
    const aliases = edge.map(id => {
        const member = fleet.find(row => row.id === id);
        if (!member) return id;
        const rank = fleet.filter(row => row.role === member.role).findIndex(row => row.id === id) + 1;
        return (member.role === 'search' ? 'S' : 'R') + rank;
    });
    const warning = {ids: edge, hasMargin: margin !== null, active: connected === true && edge.length === 2 && record?.warningActive?.[index] === true,
        detail: '병목 링크 ' + aliases.join(' ↔ ') + ' · ' + formatMargin(margin)};
    let marginReason = marginSource;
    if (margin === null) marginReason = !valid ? '이 시각에 해당하는 RF 구간 기록 없음' : !matching ? '이 실행·변환본의 검증된 RF 자료 없음' : '해당 RF 구간의 전파 자료 또는 연결 판정 검증값 부족';
    return {connected, margin, marginReason, marginSeries, risk: recordedRisk(run, time),
        marginPath: margin === null ? [] : ids(record.paths?.[record.pathIndices?.[index]]),
        marginPair: margin === null ? [] : ids(record.requiredPairs?.[record.pairIndices?.[index]]),
        warning, nodes: describeNodes(fleet, graph, warning), route: availableRoute(graph, a, b), groups: graph?.groups || []};
}

export function warningRuleText() {
    return '연결된 수색망에서 우회 경로까지 고려한 병목 링크의 양 끝 기체를 표시합니다. 여유가 ' + warningThresholds.enter + ' dB 이하이면 노랑, ' + warningThresholds.exit + ' dB 이상으로 회복하면 역할색으로 돌아갑니다.';
}
