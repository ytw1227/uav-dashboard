// These commands form the boundary between Python page selection and the local
// playback clocks. Original switchFlight preserves a clipped time and pair.
let mountedSource;
let published = '';
let publishTimer;
let selectionTicket = 0;
let comparisonTimer;
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
        ticket: selectionTicket,
        team_context: teamContext(),
        run_id: S.runId,
        revision: S.revision,
        region: mapKey(S.manifest),
        mode: S.manifest?.mode || null,
        team_interval: teamManifest()?.decision_interval_s ?? null,
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
    if (preservePair && !S.flightLoading && S.scene && S.runId === id && S.revision === revision &&
        surface.dataset.loadState !== 'error') return clientState();
    const mode = metadata(row).mode;
    const mate = (teamPair(selectedCatalogRun()) || pairedFlights(S.runs, selectedCatalogRun()))?.[mode];
    if (preservePair && !S.flightLoading && S.scene && mate?.run_id === id && revisionOf(mate) === revision) {
        await switchFlight(mode);
    } else {
        await selectRun(id, revision);
    }
    publishSelection();
    return clientState();
}

export async function initialize(source, preferredRun, preferredRevision, deferInitial=false) {
    mountedSource = source;
    S.runs = window.__NICEGUI_CATALOG__.runs;
    if (!S.runs.length) throw new Error('이 자료에 재생 가능한 실행이 없습니다.');
    window.__SWARM_DEBUG__ = {state:S, selectRun, setTime, selectEvent};
    window.uavReplay = Object.freeze({
        selectRun: (id, revision, ticket=0) => openArchive(id, revision, ticket, false),
        openRun: (id, revision, ticket=0) => openArchive(id, revision, ticket, true),
        openTeam,
        suspend: () => {
            S.playing = false;
            if (incidentReplay.snapshot().open) incidentReplay.reset();
            publishSelection();
        },
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
        clearTimeout(comparisonTimer);
        classes.disconnect();
        incidentReplay.reset();
    }, {once: true});
    // The regional page chooses the team's pinned edition before opening a flight.
    if (deferInitial) return;
    const defaultId = window.__NICEGUI_CATALOG__.scope.flight_pairs[
        window.__NICEGUI_CATALOG__.scope.maps[0]
    ]?.v21;
    const preferred = S.runs.find(row => row.run_id === (preferredRun || defaultId));
    if (!preferred) throw new Error('지정한 실행을 찾을 수 없습니다.');
    await chooseRun(preferred.run_id, preferredRevision || revisionOf(preferred));
}

async function openArchive(id, revision, ticket, restart) {
    selectionTicket = ticket;
    surface.removeAttribute('aria-busy');
    const changed = !!teamContext();
    S.runs = setTeamFlight(null);
    surface.removeAttribute('data-team-result');
    return chooseRun(id, revision, !restart && !changed);
}

async function openTeam(region, id, revision, sha, mode, ticket, restart) {
    selectionTicket = ticket;
    surface.setAttribute('aria-busy', 'true');
    surface.dataset.loadState = 'loading';
    if (teamContext() !== id || revisionOf(teamManifest()) !== revision) {
        clearTimeout(comparisonTimer);
        S.pairCompareRequest = (S.pairCompareRequest || 0) + 1;
        ++performanceRequest;
        if (S.tab === 'compare') $('pairedTable').textContent = '선택한 비행의 비교 결과를 읽는 중…';
        if (S.tab === 'performance') $('performanceContent').textContent = '선택한 비행의 모델 정보를 읽는 중…';
    }
    let record;
    try {
        record = await teamDescriptor(region, id, revision, sha);
    } catch (error) {
        if (selectionTicket === ticket) {
            surface.dataset.loadState = 'error';
            surface.removeAttribute('aria-busy');
        }
        return {...clientState(), open_error:String(error.message || error)};
    }
    if (selectionTicket !== ticket) return clientState();
    const row = mode === 'nominal' ? record.nominal : record.row;
    if (!row) throw new Error('이 지역에 저장된 기본 대형 비행이 없습니다.');
    const sameEdition = teamContext() === id && revisionOf(teamManifest()) === revision;
    S.runs = setTeamFlight(record);
    surface.dataset.teamResult = id;
    // Even the same nominal pin needs new comparison/model-tab state when its context changes.
    await chooseRun(row.run_id, revisionOf(row), !restart && sameEdition);
    if (selectionTicket === ticket) {
        surface.removeAttribute('aria-busy');
        publishSelection();
        if (surface.dataset.loadState === 'error') return {...clientState(), open_error:$('notice').textContent};
    }
    return clientState();
}

async function teamComparison(request) {
    clearTimeout(comparisonTimer);
    const manifest = teamManifest();
    const current = () => request === S.pairCompareRequest && S.tab === 'compare' &&
        teamContext() === manifest.run_id && revisionOf(teamManifest()) === revisionOf(manifest);
    $('pairedComparison').classList.remove('hidden');
    $('legacyComparison').classList.add('hidden');
    $('pairedHeading').textContent = mapName(manifest) + ' · 같은 조건의 비행 비교';
    const interference = {continuous:'지속 간섭', intermittent:'간헐 간섭', clear:'간섭 없음'}[manifest.kind] || manifest.kind;
    const placement = {east_west:'동서 배치', north_south:'남북 배치'}[manifest.direction] || manifest.direction;
    const conditions = `같은 지도 · ${interference} · ${placement} · seed ${manifest.seed} · 팀 코드 ${manifest.team_commit.slice(0,7)}`;
    const modeNote = metadata(selectedCatalogRun()).mode === 'nominal'
        ? ' · 지도에서 재생한 기본 대형은 이전 기록입니다. 이 표는 선택한 팀 버전으로 실행한 두 비행을 비교합니다.' : '';
    $('pairedConditions').textContent = conditions;
    $('pairedTable').textContent = '두 비행 결과를 읽는 중…';
    async function refresh() {
        if (!current()) return;
        try {
            const data = await readTeamComparison(manifest);
            if (!current()) return;
            if (data.status === 'ready') {
                $('pairedConditions').textContent = conditions + ' · 각 방식 1회' + modeNote;
                $('pairedTable').innerHTML = pairedTable(data.nominal, data.applied);
            } else {
                $('pairedConditions').textContent = conditions + ' · ' + (data.message || '기본 대형 비교 결과 대기 중') + modeNote;
                $('pairedTable').innerHTML = pairedTable({}, manifest);
                comparisonTimer = setTimeout(refresh, 5000);
            }
        } catch (error) {
            if (current()) {
                $('pairedConditions').textContent = conditions + ' · ' + error.message + modeNote;
                $('pairedTable').innerHTML = pairedTable({}, manifest);
            }
        }
    }
    await refresh();
}
