// These commands form the boundary between Python page selection and the local
// playback clocks. Original switchFlight preserves a clipped time and pair.
let mountedSource;
let published = '';
let publishTimer;
const surface = document.querySelector('.uav-replay');
const retryRead = document.createElement('button');
retryRead.type = 'button';
retryRead.className = 'button compact';
retryRead.textContent = '이 비행 다시 읽기';
retryRead.hidden = true;
retryRead.onclick = () => selectRun(S.runId, S.revision);
document.getElementById('notice').after(retryRead);

function clientState() {
    const incident = incidentReplay.snapshot();
    return {
        source: mountedSource,
        run_id: S.runId,
        revision: S.revision,
        region: mapKey(S.manifest),
        mode: S.manifest?.mode || null,
        tab: S.tab,
        observer: S.node,
        peer: S.peer,
        time: S.time,
        playing: S.playing,
        event_id: S.eventId,
        incident_open: incident.open,
        incident_id: incident.open ? incident.eventId : null,
        incident_time: incident.open ? incident.time : null,
        loading: !!S.flightLoading,
        read_error: surface.dataset.loadState === 'error',
    };
}

function publishSelection() {
    if (!mountedSource) return;
    const notice = document.getElementById('notice');
    const failed = !S.flightLoading && notice.classList.contains('error') && !notice.classList.contains('hidden');
    surface.dataset.loadState = S.flightLoading ? 'loading' : failed ? 'error' : 'ready';
    retryRead.hidden = !failed;
    // Coalesce slider events; the server never receives geometry or every frame.
    clearTimeout(publishTimer);
    publishTimer = setTimeout(() => {
        const state = clientState();
        const signature = JSON.stringify(state);
        if (signature !== published) {
            published = signature;
            window.emitEvent('uav_replay_state', state);
        }
    }, 65);
}

async function chooseRun(id, revision, preservePair=true) {
    const row = S.runs.find(row => row.run_id === id && revisionOf(row) === revision);
    if (!row) throw new Error('이 자료에 없는 실행 또는 변환본입니다.');
    if (preservePair && S.runId === id && S.revision === revision && surface.dataset.loadState !== 'error') return clientState();
    const mode = metadata(row).mode;
    const mate = pairedFlights(S.runs, selectedCatalogRun())?.[mode];
    if (preservePair && !S.flightLoading && S.scene && mate?.run_id === id && revisionOf(mate) === revision) {
        await switchFlight(mode);
    } else {
        await selectRun(id, revision);
    }
    publishSelection();
    return clientState();
}

export async function initialize(source, preferredRun, preferredRevision) {
    mountedSource = source;
    S.runs = window.__NICEGUI_CATALOG__.runs;
    if (!S.runs.length) throw new Error('이 자료에 재생 가능한 실행이 없습니다.');
    window.__SWARM_DEBUG__ = {state:S, selectRun, setTime, selectEvent};
    window.uavReplay = Object.freeze({
        selectRun: chooseRun,
        openRun: (id, revision) => chooseRun(id, revision, false),
        setTab: tab => {setTab(tab); publishSelection(); return clientState();},
        setMode: async mode => {await switchFlight(mode); publishSelection(); return clientState();},
        snapshot: clientState,
    });
    // The renderer toggles classes on the body for fullscreen and eye-line
    // controls. Mirror them on the scoped CSS boundary without styling NiceGUI.
    const syncClasses = () => {
        for (const name of ['bundang-visual-active', 'bm-show-actions', 'bm-map-expanded']) {
            surface.classList.toggle(name, document.body.classList.contains(name));
        }
    };
    const classes = new MutationObserver(syncClasses);
    classes.observe(document.body, {attributes: true, attributeFilter: ['class']});
    syncClasses();
    for (const event of ['click', 'change', 'input', 'pointerup', 'close']) {
        surface.addEventListener(event, publishSelection, true);
    }
    document.addEventListener('keyup', publishSelection);
    // A whole-page route change releases all scoped components, Plotly canvases,
    // document keyboard listeners and local clocks. No state is stored globally
    // across tabs, apart from the original evidence display preference.
    window.addEventListener('pagehide', () => {
        S.playing = false;
        clearTimeout(publishTimer);
        classes.disconnect();
        incidentReplay.reset();
    }, {once: true});
    const defaultId = window.__NICEGUI_CATALOG__.scope.flight_pairs[
        window.__NICEGUI_CATALOG__.scope.maps[0]
    ]?.v21;
    const preferred = S.runs.find(row => row.run_id === (preferredRun || defaultId));
    if (!preferred) throw new Error('지정한 실행을 찾을 수 없습니다.');
    await chooseRun(preferred.run_id, preferredRevision || revisionOf(preferred));
}
