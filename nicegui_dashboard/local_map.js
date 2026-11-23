import {MapRenderer, positionAt, before, intervalAt, completedDecisionAt, chartSVG} from './render.js';
import {coreTimeSeriesKeys, timeSeriesDefinition, isFinalV1} from './library.js';

// A canvas is owned by one NiceGUI client and one selected revision.
const canvases = new WeakMap();
const canvasFor = id => document.getElementById('c' + id);
function halt(state) {
    cancelAnimationFrame(state.frame);
    state.frame = null;
}

export function cameraView(id) {
    const renderer = canvases.get(canvasFor(id))?.renderer;
    if (!renderer) return null;
    return {zoom: renderer.zoom, pan: [...renderer.pan], camera: {...renderer.camera}, viewMode: renderer.viewMode};
}

export function setup(id, label, chart, note, manifest, scene, camera = null) {
    const canvas = canvasFor(id);
    if (!canvas) return;
    const renderer = new MapRenderer(canvas, detail => {
        canvas.dispatchEvent(new CustomEvent('local-pair', {detail}));
    });
    if (camera) {
        renderer.zoom = camera.zoom;
        renderer.pan = camera.pan;
        renderer.camera = camera.camera;
        renderer.viewMode = camera.viewMode;
    }
    const userCamera = () => canvas.dispatchEvent(new CustomEvent('local-camera'));
    canvas.addEventListener('pointerdown', userCamera);
    canvas.addEventListener('wheel', userCamera, {passive: true});
    canvas.addEventListener('keydown', event => { if (event.key === 'Home') userCamera(); });
    const state = {canvas, label: canvasFor(label), chart: canvasFor(chart), note: canvasFor(note),
        manifest, scene, renderer, frame:null, key:'search_connected', options:{}, full:false};
    canvases.set(canvas, state);
    state.chart.addEventListener('click', event => {
        const svg = state.chart.querySelector('svg');
        if (!svg || !state.range) return;
        const point = new DOMPoint(event.clientX,event.clientY).matrixTransform(svg.getScreenCTM().inverse());
        const ratio = Math.max(0, Math.min(1,(point.x-48)/(720-48-18)));
        const t = state.range[0]+ratio*(state.range[1]-state.range[0]);
        state.chart.dispatchEvent(new CustomEvent('local-seek',{detail:{t}}));
    });
}

function chartDefinition(state, key) {
    const recorded = state.manifest.series_definitions?.[key] || {};
    const unit = key === 'search_connected' ? 'boolean' : 'ratio';
    return timeSeriesDefinition(key, recorded, {unit});
}

function drawChart(state) {
    const data = state.full ? state.fullData : state.data;
    if (!data) return;
    const frames = data.rf_frames;
    const range = data.range || data.chart_range;
    const keys = coreTimeSeriesKeys(state.manifest, new Set(frames.flatMap(f => Object.keys(f.metrics || {}))));
    state.options = Object.fromEntries(keys.map(k=>[k,chartDefinition(state,k).label]));
    if (!keys.includes(state.key)) state.key = keys[0];
    state.range = range;
    const d = chartDefinition(state, state.key);
    const connected = ['search_connected','search_pair_reachability'].includes(state.key);
    const when = state.manifest.series_definitions?.[state.key]?.available_at ||
        (connected ? 'interval_start_s' : 'available_at_s');
    const points = frames.map(f=>({t:f[when] ?? f.available_at_s, v:f.metrics?.[state.key]}));
    const final = isFinalV1(state.manifest);
    const events = data.events.filter(e=>final ? e.type==='persistent_search_outage' :
        e.scope==='search_network' || e.type?.includes('search'));
    state.chart.innerHTML = chartSVG(points,{selected:-Infinity,window:range,unit:d.unit,
        events,label:d.label || '보관 RF', min:['ratio','boolean'].includes(d.unit)?0:null,
        max:['ratio','boolean'].includes(d.unit)?1:null});
    const svg = state.chart.querySelector('svg');
    svg.style.cssText = 'width:100%;max-height:210px;display:block;';
    const marker = document.createElementNS('http://www.w3.org/2000/svg','path');
    marker.setAttribute('stroke','#193b5a');
    marker.setAttribute('stroke-dasharray','4 3');
    marker.setAttribute('data-playhead','');
    svg.append(marker);
    state.marker = marker;
    state.note.textContent = (d.description || '이 구간에 수록된 지표가 없습니다.') +
        ' 붉은 영역은 ' + (final?'1초 이상 지속 수색망 단절':'수색망 단절') +
        '입니다. 표시 구간 ' + range[0].toFixed(1)+'–'+range[1].toFixed(1)+'초';
    drawPlayhead(state);
}

function drawPlayhead(state) {
    const t = state.data?.t;
    if (!state.marker || !state.range) return;
    const [start,end] = state.range;
    const x = 48+(t-start)/(end-start || 1)*(720-48-18);
    state.marker.setAttribute('d', t>=start && t<=end ? 'M'+x+' 14V123' : '');
}

export function metric(id,key) {
    const state = canvases.get(canvasFor(id));
    if (state && key in state.options) {
        state.key = key;
        drawChart(state);
    }
}

export function windowPlot(id) {
    const state = canvases.get(canvasFor(id));
    if (state) {state.full=false;drawChart(state);}
}

export function cachedPlot(id) {
    const state = canvases.get(canvasFor(id));
    if (!state?.fullData) return false;
    state.full=true;
    drawChart(state);
    return true;
}

export function fullPlot(id, data) {
    const state = canvases.get(canvasFor(id));
    if (state) {
        state.fullData=data;
        cachedPlot(id);
    }
}

function draw(state) {
    const d = state.data;
    if (!d) return;
    state.renderer.update({
        scene: state.scene, positions: d.positions, position: positionAt(d.positions, d.t),
        frame: intervalAt(d.rf_frames, d.t), decision: before(d.decisions, d.t, 'decision_time_s'),
        completedDecision: completedDecisionAt(d.decisions, d.t), selectedNode: d.node,
        selectedPeer: d.peer, event: d.event, time: d.t,
        layers: {tracks: true, links: true, actions: true, future: false},
    });
    state.canvas.setAttribute('aria-label', '보관 비행 지도 · ' + d.t.toFixed(1) + '초');
    const connected = intervalAt(d.rf_frames, d.t)?.metrics?.search_connected;
    if (state.label) state.label.textContent = d.t.toFixed(1) + '초 · ' +
        ({0:'수색망 단절', 1:'수색망 연결'}[connected] || '현재 RF 구간 미기록');
    drawPlayhead(state);
}

export function show(id, data) {
    const state = canvases.get(canvasFor(id));
    if (!state) return;
    if (state.data && state.data.request_seq > data.request_seq) return;
    halt(state);
    state.data = data;
    drawChart(state);
    draw(state);
    if (data.resume) command(id, 'toggle');
    return {selected:state.key || null, options:state.options};
}

export function playbackTime(id) {
    return canvases.get(canvasFor(id))?.data?.t ?? null;
}

export function command(id, cmd) {
    const state = canvases.get(canvasFor(id));
    if (!state) return;
    if (cmd === '2d' || cmd === '3d') return state.renderer.setViewMode(cmd);
    if (cmd === 'reset') return state.renderer.reset();
    const d = state.data;
    if (cmd !== 'toggle' || !d) return;
    const sendTime = (resume = false) => state.canvas.dispatchEvent(
        new CustomEvent('local-time', {detail: {t: d.t, resume, request_seq: d.request_seq}}));
    if (state.frame !== null) {
        halt(state);
        sendTime();
        return;
    }
    if (d.t >= d.end) {
        if (d.start === d.end) return;
        d.t = d.start;
        sendTime(true);
        return;
    }
    let last = performance.now();
    const tick = now => {
        if (!state.canvas.isConnected) return halt(state);
        d.t = Math.min(d.cache_end, d.t + (now - last) / 1000);
        last = now;
        draw(state);
        if (d.t < d.cache_end) {
            state.frame = requestAnimationFrame(tick);
        } else {
            halt(state);
            sendTime(d.t < d.end);
        }
    };
    state.frame = requestAnimationFrame(tick);
}
