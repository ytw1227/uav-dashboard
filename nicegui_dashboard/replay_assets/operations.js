// Read-only flight operations. No thresholds, flight decisions, or KPI definitions live here.
const finite = value => typeof value === 'number' && Number.isFinite(value);
const fields = manifest => ({...manifest?.metadata, ...manifest});
const revisionOf = manifest => manifest?.bundle_revision || manifest?.revision;
const textNumber = (value, digits=1) => finite(value) ? value.toLocaleString('ko-KR', {
    minimumFractionDigits:digits, maximumFractionDigits:digits}) : '—';
const atTime = (value, digits=1) => textNumber(value, digits) + '초';
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
const DUTIES = {search:'수색', mobile_relay:'일반 중계', fixed_relay:'고정 중계', altitude_relay:'고도 조정 중계'};
export const OPERATION_ROLE_COLORS = Object.freeze({fixed_relay:'#4d79ed', altitude_relay:'#4d79ed'});
export function operationRoleColor(role, status, fallback) {
    // Operational duties never override low-margin, separation, or unknown states.
    return status === 'connected' && role?.role === 'relay' ? OPERATION_ROLE_COLORS[role.duty] || fallback : fallback;
}
const STAGES = {
    climb:['고도를 높이는 중', '기록된 목표 높이로 이동합니다. 통신 조건의 확인은 별도 단계입니다.'],
    entry_wait:['통신 확인 대기', '수색 진행을 잠시 멈추고 통과에 필요한 연결을 확인합니다.'],
    crossing:['해당 구간 통과 중', '통신 확인 뒤 계획된 구간을 진행합니다.'],
    descent:['안전 하강 중', '기록된 다음 높이로 내려가는 중입니다.'],
    descent_check:['하강 후 통신 확인', '더 내려가기 전에 현재 높이에서 연결을 확인합니다.'],
    recovery:['이전 높이로 복원 중', '하강 뒤 확인 조건을 충족하지 못해 이전 높이로 돌아갑니다.'],
    clearing:['경로 정체 해소 중', '안전한 우회 이동이나 다른 기체의 비켜주기로 다음 수색 지점까지 통로를 확보합니다.'],
    search:['현재 높이로 비행', '현재 배치를 유지하며 수색 경로를 진행합니다.'],
    holding:['계획에 따른 이동 대기', '운용 기록에 이동 대기 상태가 남아 있습니다.'],
    complete:['수색 경로 완료', '경로 지점에 모두 도달했습니다. 전체 임무 결과는 최종 결과를 따릅니다.'],
};

function droneName(id, fleet=[]) {
    const node = fleet.find(item => item.id === id);
    if (!node || !['search','relay'].includes(node.role)) return String(id ?? '기체');
    return (node.role === 'search' ? 'S' : 'R') + (fleet.filter(item => item.role === node.role)
        .findIndex(item => item.id === id) + 1);
}

function previousIndex(rows, time, key='time_s') {
    let low=0, high=rows.length;
    while (low < high) {
        const middle=(low+high) >>> 1;
        if (rows[middle][key] <= time) low=middle+1;
        else high=middle;
    }
    return low-1;
}

export function operationIdentityMatches(data, manifest) {
    const m=fields(manifest), identity=data?.identity;
    if (!identity || identity.run_id !== m.run_id || identity.revision !== revisionOf(m)) return false;
    return ['team_commit','map_hash','model_sha256'].every(key => !m[key] || identity[key] === m[key]);
}

export function operationEligible(manifest) {
    const m=fields(manifest);
    return m.adapter_id === 'team-halfsec' && m.controller === 'halfsec_planned_eight' && Boolean(m.region) &&
        ['v21','v21_halfsec'].includes(m.mode) &&
        /^[\w-]+$/.test(m.run_id || '') && /^[\w-]+$/.test(revisionOf(m) || '');
}

// Actual altitude follows the same linear interpolation used by the map. A
// future progress/state record is never used to describe the current decision.
function currentHeight(state, row, id) {
    const positions=state.overview?.positions || [], time=state.time;
    const i=previousIndex(positions,time,'sample_time_s'), a=positions[i], b=positions[i+1];
    const first=a?.uavs?.find(item => item.id === id), second=b?.uavs?.find(item => item.id === id);
    if (finite(first?.z)) {
        const span=b?.sample_time_s-a.sample_time_s;
        const k=span > 0 ? (time-a.sample_time_s)/span : 0;
        return {value:finite(second?.z) && k > 0 && k < 1 ? first.z+(second.z-first.z)*k : first.z,
            interpolated:Boolean(finite(second?.z) && k > 0 && k < 1), time_s:a.sample_time_s};
    }
    const index=Number(String(id).match(/^uav-(\d+)$/)?.[1]);
    return {value:Number.isInteger(index) && finite(row?.heights_m?.[index]) ? row.heights_m[index] : null,
        interpolated:false, time_s:row?.time_s};
}

export function operationReadout(data, state) {
    if (!data || data.status !== 'available' || !state || !operationIdentityMatches(data,state.manifest)) return null;
    const rows=data.progress || [], row=rows[previousIndex(rows,state.time)];
    if (!row) return null;
    const roles=data.roles || [], id=state.observer ?? state.node ?? roles[0]?.drone_id;
    const role=roles.find(item => item.drone_id === id);
    const altitudeId=row.altitude_relay_id || roles.find(item => item.duty === 'altitude_relay')?.drone_id;
    const height=currentHeight(state,row,id);
    let stage=STAGES[row.state], stageKey=row.state;
    if (row.motion_pending === true) {
        stageKey='height_wait';
        stage=['고도 조정 대기','수평 이동을 마친 뒤 높이 이동을 시작하도록 대기합니다.'];
    } else if (finite(row.move_start_s) && row.move_start_s > state.time && ['climb','descent','recovery'].includes(row.state)) {
        stageKey='command_wait';
        stage=['고도 명령 적용 대기','기록된 높이 이동 시작 시각 '+atTime(row.move_start_s)+' 전입니다.'];
    }
    const waiting=row.entry_waiting === true || row.state === 'entry_wait' || row.state === 'holding';
    let progressLabel='수색 경로 진행 중';
    if (row.state === 'complete') progressLabel=state.time < data.summary?.duration_s ? '수색 경로 완료 · 공유 마무리' : '비행 종료 · 수색 경로 완료';
    else if (row.clearing === true || row.state === 'clearing') progressLabel='경로 정체 해소 중';
    else if (waiting) progressLabel=row.entry_waiting ? '통신 확인을 위한 수색 대기' : '계획에 따른 이동 대기';
    return {
        time:state.time, recordedTime:row.time_s,
        selected:{id, role:role ? {...role,label:DUTIES[role.duty] || role.label} : null,
            height:height.value, heightInterpolated:height.interpolated,
            targetHeight:id === altitudeId && finite(row.target_height_m) ? row.target_height_m : null},
        stage:{key:stageKey, label:stage?.[0] || '운용 상태 기록 확인',
            detail:stage?.[1] || '저장된 상태 이름: '+String(row.state || '없음'),
            droneId:['clearing','complete'].includes(stageKey) ? null : altitudeId},
        progress:{label:progressLabel, intentionalWait:waiting,
            reached:finite(row.waypoints_reached) ? row.waypoints_reached : row.stage,
            total:row.waypoints_total, coverage:row.coverage,
            unchangedSeconds:finite(row.stalled_s) ? row.stalled_s : null},
        row,
    };
}

export function operationRecords(data) {
    const records=[];
    const add=(id,time,label,detail,droneId) => {
        if (finite(time)) records.push({id,time,label,detail,droneId});
    };
    for (const [i,window] of (data?.windows || []).entries()) {
        const id=window.id || 'window-'+i, who=window.drone_id;
        add(id+'-start',window.started_s,'고도 조정 구간 시작',
            finite(window.height_m) ? '계획 높이 '+textNumber(window.height_m)+' m' : '',who);
        add(id+'-ready',window.ready_s,'계획 높이 도달','높이 도달과 통신 확인은 별도 단계입니다.',who);
        add(id+'-entry',window.entry_request_s,'통과 전 통신 확인 시작','수색 진행을 대기하며 연결을 확인합니다.',who);
        add(id+'-verified',window.verified_s,'통신 확인 완료 · 통과 시작','기록된 통과 확인 시각입니다.',who);
        const outcomes={crossed:'고도 조정 구간 통과 완료',entry_timeout:'통신 확인 대기 제한 도달',
            passed_before_proof:'통신 확인 전 구간 종료',skipped:'고도 조정 구간 건너뜀'};
        if (outcomes[window.outcome]) add(id+'-finish',window.finished_s,
            outcomes[window.outcome],'저장된 구간 종료 기록',who);
    }
    for (const [i,move] of (data?.moves || []).entries()) {
        const labels={climb:'상승',descent:'안전 하강',recovery:'이전 높이 복원'};
        const label=labels[move.kind] || '높이 이동';
        const detail=finite(move.start_m) && finite(move.target_m)
            ? textNumber(move.start_m)+' → '+textNumber(move.target_m)+' m' : '';
        add('move-'+i+'-request',move.request_s,label+' 요청',detail,move.drone_id);
        add('move-'+i+'-start',move.start_s,label+' 시작',detail,move.drone_id);
        add('move-'+i+'-end',move.end_s,label+' 예정 종료',detail+' · 이동 계획에 기록된 종료 시각',move.drone_id);
    }
    for (const [i,check] of (data?.checks || []).entries()) {
        if (typeof check.passed !== 'boolean') continue;
        const labels={descent_safe:'다음 하강 높이 검토',confirmation:'통신 유지 확인'};
        if (!labels[check.kind]) continue;
        add('check-'+i,check.time_s,labels[check.kind]+' · '+(check.passed?'조건 충족':'조건 미충족'),
            [finite(check.target_m)?'검토 높이 '+textNumber(check.target_m)+' m':'',
                '검사 결과이며 이동 명령의 실행 여부와 다릅니다.'].filter(Boolean).join(' · '),check.drone_id);
    }
    return records.filter(record=>!finite(data?.summary?.duration_s) || record.time<=data.summary.duration_s)
        .sort((a,b) => a.time-b.time || a.id.localeCompare(b.id));
}

function pinKey(manifest) {
    const m=fields(manifest);
    return [m.run_id,revisionOf(m),m.team_commit,m.map_hash,m.model_sha256].join('|');
}

export function createOperations({host, Plot=globalThis.Plotly, onSeek=()=>{}, onSelectDrone=()=>{},
    onRolesChanged=()=>{}, fetchJSON}={}) {
    if (!host) throw new Error('운용 표시 영역이 필요합니다.');
    let state=null, data=null, key='', request=0, abort=null, disposed=false, lastReadout=null;
    let records=[], allRecords=[], plotKey='', plotBusy=false, plotPending=false, plotQueued=false, pointer=null;
    let lastRecord=-1, selectedRole='', lastBasis='', resizeFrame=null;
    const read=fetchJSON || (async (path,options) => {
        const response=await fetch(path,options);
        if (!response.ok) throw new Error('운용 기록 조회 실패 ('+response.status+')');
        return response.json();
    });
    host.classList.add('uav-operations');
    host.hidden=true;
    host.innerHTML=`<div class="ops-heading"><div><span class="ops-eyebrow">같은 시각의 비행 기록</span><h3>배치와 비행 운용</h3></div><span class="ops-time" data-ops="time"></span></div>
        <p class="ops-message" data-ops="message" role="status"></p>
        <div data-ops="body" hidden>
          <p class="ops-plan" data-ops="plan"></p>
          <div class="ops-roles" data-ops="roles" aria-label="운용 역할별 기체 선택"></div>
          <div class="ops-current">
            <section class="ops-card"><span class="ops-label" data-ops="selected-label">선택 기체의 실제 높이</span><strong class="ops-value" data-ops="height"></strong><span class="ops-support" data-ops="target"></span><small data-ops="height-note"></small></section>
            <section class="ops-card"><span class="ops-label" data-ops="stage-label">고도 운용</span><strong class="ops-state" data-ops="stage"></strong><span class="ops-support" data-ops="stage-note"></span></section>
            <section class="ops-card"><span class="ops-label">수색 진행</span><strong class="ops-state" data-ops="progress-state"></strong><span class="ops-support" data-ops="progress"></span><small data-ops="wait"></small></section>
          </div>
          <details class="ops-details" data-ops="chart-details"><summary>고도와 통신 여유 함께 보기</summary>
            <div class="ops-chart-tools"><span data-ops="chart-title"></span><div><button type="button" data-ops-action="full">전체 비행</button><button type="button" data-ops-action="near">재생 시각 주변</button></div></div>
            <div class="ops-plot" data-ops="plot" aria-label="고도와 전체 수색망 통신 여유 시간 그래프"></div>
            <p class="ops-graph-note">위: 선택 기체의 실제 높이와 기록된 목표 높이 · 아래: 전체 수색망 통신 여유. 점선은 재생 시각입니다. 그래프를 누르면 이동하고, 드래그하면 확대합니다.</p>
            <p class="ops-graph-note">고도 변화와 통신 변화를 함께 확인하는 자료입니다. 그래프만으로 고도 조정이 복구의 유일한 원인이라고 판단하지 않습니다.</p>
          </details>
          <details class="ops-details" data-ops="records-details"><summary>고도 운용 기록 <span data-ops="record-count"></span></summary><div class="ops-record-tools"><label>표시할 기록 <select data-ops="record-filter"><option value="motion">단계와 높이 이동</option><option value="all">통신 검사도 함께 보기</option></select></label></div><div class="ops-records" data-ops="records"></div></details>
          <details class="ops-details" data-ops="basis-details" hidden><summary>선택한 사건의 판단·명령 기록</summary><div class="ops-basis" data-ops="basis"></div></details>
        </div>`;
    const el=name => host.querySelector('[data-ops="'+name+'"]');
    let plot=el('plot');
    const set=(name,value) => {const node=el(name);if(node.textContent !== value)node.textContent=value;};
    const getName=id => droneName(id, fields(state?.manifest).fleet || []);
    const duration=() => data?.summary?.duration_s ?? fields(state?.manifest).simulation_end_s ?? data?.progress?.at(-1)?.time_s ?? 0;

    function message(value) {
        el('message').hidden=!value;
        el('message').textContent=value;
        el('body').hidden=Boolean(value);
    }
    function clearPlot() {
        plotKey='';
        // A new flight gets its own element. In-flight Plotly promises cannot
        // paint into the next flight while old traces are being released.
        const old=plot;
        plot=old.cloneNode(false);
        old.replaceWith(plot);
        bindPlotPointer();
        if (Plot && old._fullLayout && !plotBusy) Plot.purge(old);
    }
    function reset() {
        ++request;
        abort?.abort();abort=null;
        state=null;data=null;key='';records=[];allRecords=[];lastReadout=null;selectedRole='';lastRecord=-1;lastBasis='';pointer=null;
        host.hidden=true;
        el('basis-details').hidden=true;
        el('basis').textContent='';
        set('time','');
        clearPlot();
        onRolesChanged([]);
    }
    function selectDrone(id) {
        if (!data?.roles?.some(role => role.drone_id === id)) return;
        onSelectDrone(id);
    }
    host.addEventListener('click',event => {
        const button=event.target.closest('button');
        if (!button || !host.contains(button) || !state) return;
        if (button.dataset.drone) selectDrone(button.dataset.drone);
        if (button.dataset.record) {
            const record=records.find(item => item.id === button.dataset.record);
            if (record) {if(record.droneId)selectDrone(record.droneId);onSeek(Math.min(duration(),record.time));}
        }
        if (button.dataset.opsAction === 'full') changeRange([0,Math.max(1,duration())]);
        if (button.dataset.opsAction === 'near') changeRange([Math.max(0,state.time-20),Math.min(duration(),state.time+20)]);
    });
    el('chart-details').addEventListener('toggle',() => {if(el('chart-details').open)queuePlot();});
    el('record-filter').addEventListener('change',() => {fillRecords();draw();});

    function fillRecords() {
        records=el('record-filter').value === 'all' ? allRecords : allRecords.filter(record=>!record.id.startsWith('check-'));
        lastRecord=-1;
        set('record-count',records.length ? String(records.length)+'건' : '');
        el('records').innerHTML=records.length ? records.map(record => `<button type="button" class="ops-record" data-record="${esc(record.id)}"><time>${esc(atTime(record.time))}</time><span><strong>${esc(record.label)}</strong><small>${esc([record.droneId?getName(record.droneId):'',record.detail].filter(Boolean).join(' · '))}</small></span><span class="ops-record-go">이 시각 보기 →</span></button>`).join('') : '<p class="ops-empty">이 비행에는 기록된 고도 조정 사건이 없습니다.</p>';
    }

    function fillStatic() {
        const roles=data.roles || [], station=roles.filter(role => role.duty === 'fixed_relay');
        const altitude=roles.filter(role => role.duty === 'altitude_relay');
        const plan=[];
        if (station.length) plan.push(station.map(role => getName(role.drone_id)).join('·')+'은 고정 중계');
        if (altitude.length) plan.push(altitude.map(role => getName(role.drone_id)).join('·')+'은 구간에 따라 높이 조정');
        set('plan',plan.length ? plan.join(' · ')+'. 역할은 이 비행의 배치 기록을 따릅니다.' : '이 비행에 기록된 기체별 운용 역할입니다.');
        el('roles').innerHTML=roles.map(role => `<button type="button" class="ops-role" data-drone="${esc(role.drone_id)}" data-duty="${esc(role.duty)}" aria-pressed="false">${OPERATION_ROLE_COLORS[role.duty]?`<i class="ops-role-dot" style="--ops-role-color:${OPERATION_ROLE_COLORS[role.duty]}" aria-hidden="true"></i>`:''}<b class="ops-drone-name ops-${role.role==='search'?'search':'relay'}">${esc(getName(role.drone_id))}</b><span>${esc(DUTIES[role.duty] || role.label || '중계')}</span></button>`).join('');
        allRecords=operationRecords(data);
        fillRecords();
        onRolesChanged(roles.map(role => ({...role,label:DUTIES[role.duty] || role.label})));
    }

    function drawBasis() {
        const event=typeof state.activeEvent === 'object' ? state.activeEvent :
            (state.events || []).find(item => (item.id || item.event_id) === state.activeEvent);
        const detail=event?.source_detail;
        const basisKey=event ? JSON.stringify([event.id,event.event_id,event.start_s,detail]) : '';
        if (lastBasis === basisKey) return;
        lastBasis=basisKey;
        const entries=[];
        if (detail) {
            const reasons={recovery:'단절 이후 복구 보정',prevention:'단절 전 예방 보정'};
            if (reasons[detail.reason]) entries.push(['기록된 선택',reasons[detail.reason]]);
            if (finite(detail.changed_relay)) entries.push(['보정 대상',getName('uav-'+detail.changed_relay)]);
            if (typeof detail.actual_connected_at_request === 'boolean') entries.push(['요청 당시 수색망',detail.actual_connected_at_request?'연결':'단절']);
            if (Array.isArray(detail.new_offset) && detail.new_offset.length >= 2 && detail.new_offset.every(finite)) {
                const offset=value=>'('+textNumber(value[0])+', '+textNumber(value[1])+') m';
                const before=Array.isArray(detail.old_offset) && detail.old_offset.length >= 2 && detail.old_offset.every(finite) ? offset(detail.old_offset)+' → ' : '';
                entries.push(['수평 보정 설정',before+offset(detail.new_offset)]);
            }
            if (finite(detail.requested_offset_x_m) && finite(detail.requested_offset_y_m))
                entries.push(['요청한 수평 보정',`x ${textNumber(detail.requested_offset_x_m)} m · y ${textNumber(detail.requested_offset_y_m)} m`]);
            if (finite(detail.ready_s)) entries.push(['명령 준비 시각',atTime(detail.ready_s)]);
            if (finite(detail.published_s)) entries.push(['명령 발행 기록',atTime(detail.published_s)]);
            if (typeof detail.command_received === 'boolean') entries.push(['명령 수신 확인',detail.command_received?'예':'아니오']);
            if (typeof detail.executed_as_requested === 'boolean') entries.push(['요청대로 적용',detail.executed_as_requested?'예':'아니오']);
        }
        el('basis-details').hidden=!entries.length;
        el('basis').innerHTML=entries.length ? `<dl>${entries.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl><p>선택된 보정과 남아 있는 명령 기록입니다. 수평 보정 설정은 실제 이동 거리가 아닙니다. 요청·발행만으로 실행을 단정하지 않으며 실제 이동은 지도에서 확인합니다.</p>` : '';
    }

    function draw() {
        if (!data || !state || host.hidden) return;
        const view=operationReadout(data,state);
        lastReadout=view;
        set('time',atTime(state.time));
        if (!view) {message('이 시각의 운용 기록이 없어 상태를 확인할 수 없습니다.');return;}
        message('');
        const who=getName(view.selected.id);
        set('selected-label',who+' · 실제 고도');
        set('height',textNumber(view.selected.height)+' m');
        set('target',finite(view.selected.targetHeight) ? '현재 목표 '+textNumber(view.selected.targetHeight)+' m' : '');
        set('height-note',view.selected.heightInterpolated ? '지도와 동일한 위치 기록 사이를 보간한 높이' : '저장된 실제 비행 높이');
        set('stage-label',view.stage.droneId ? getName(view.stage.droneId)+' · 고도 운용' : '현재 운용');
        set('stage',view.stage.label);
        set('stage-note',view.stage.detail);
        set('progress-state',view.progress.label);
        const progress=[];
        if (finite(view.progress.reached) && finite(view.progress.total)) progress.push('경로 지점 '+textNumber(view.progress.reached,0)+' / '+textNumber(view.progress.total,0));
        if (finite(view.progress.coverage)) progress.push('관측 영역 '+textNumber(view.progress.coverage*100)+'%');
        set('progress',progress.join(' · '));
        const paused=view.progress.unchangedSeconds;
        set('wait',finite(paused) && paused > 0 && view.stage.key !== 'complete'
            ? (view.progress.intentionalWait ? '대기 중 · ' : '')+'같은 경로 지점 '+atTime(paused)+' 유지'+
              (view.progress.intentionalWait ? '' : ' · 기체의 정지나 오류를 뜻하지 않습니다.') : '');
        if (selectedRole !== view.selected.id) {
            selectedRole=view.selected.id;
            for (const button of el('roles').querySelectorAll('button')) button.setAttribute('aria-pressed',String(button.dataset.drone === selectedRole));
        }
        const index=previousIndex(records,state.time,'time');
        if (index !== lastRecord) {
            if (lastRecord >= 0) el('records').children[lastRecord]?.removeAttribute('aria-current');
            if (index >= 0) el('records').children[index]?.setAttribute('aria-current','true');
            lastRecord=index;
        }
        drawBasis();
        queuePlot();
    }

    async function load(manifest, expectedKey) {
        const ticket=++request;
        abort?.abort();abort=new AbortController();
        const m=fields(manifest);
        message('이 비행의 배치·고도 운용 기록을 읽는 중…');
        try {
            const result=await read('/part2/team/'+encodeURIComponent(m.run_id)+'/'+encodeURIComponent(revisionOf(m))+'/operations', {signal:abort.signal});
            if (disposed || ticket !== request || key !== expectedKey) return;
            if (result.status !== 'available') {
                message(result.status === 'unsupported' ? '이 비행에는 운용 보조 표시가 아직 적용되지 않았습니다.' : '이 비행에는 확인된 고도·진행 기록이 없어 운용 단계를 표시할 수 없습니다.');
                return;
            }
            if (result.schema_version !== 1 || !operationIdentityMatches(result,manifest)) throw new Error('선택한 비행과 운용 기록의 출처가 일치하지 않습니다.');
            if (!Array.isArray(result.progress) || result.progress.some((row,i) => !finite(row.time_s) || (i && row.time_s < result.progress[i-1].time_s))) throw new Error('운용 기록의 시간 순서를 확인할 수 없습니다.');
            data=result;fillStatic();draw();
        } catch (error) {
            if (disposed || ticket !== request || key !== expectedKey || error?.name === 'AbortError') return;
            message('운용 기록을 확인할 수 없습니다. '+String(error.message || error));
        }
    }

    function queuePlot() {
        if (!state || !data || !el('chart-details').open || host.hidden || disposed) return;
        if (plotBusy) {plotPending=true;return;}
        if (plotQueued) return;
        plotQueued=true;
        requestAnimationFrame(() => {plotQueued=false;drawPlot();});
    }
    function plotShapes(time) {
        return [{type:'line',xref:'x',yref:'paper',x0:time,x1:time,y0:0,y1:1,line:{color:'#294b63',width:1.5,dash:'dot'}},
            {type:'line',xref:'x2 domain',yref:'y2',x0:0,x1:1,y0:0,y1:0,line:{color:'#647b8c',width:1,dash:'dash'}}];
    }
    async function drawPlot() {
        if (!state || !data || disposed || host.hidden || !el('chart-details').open || pointer) return;
        if (!Plot) {plot.textContent='그래프 구성요소를 읽을 수 없습니다. 위 수치와 운용 기록은 확인할 수 있습니다.';return;}
        const expectedKey=key, ticket=request, targetPlot=plot, observer=state.observer ?? state.node ?? data.roles?.[0]?.drone_id;
        const figureKey=key+'|'+observer;
        const time=state.time, rows=data.progress, name=getName(observer);
        plotBusy=true;plotPending=false;
        try {
            if (plotKey !== figureKey || !targetPlot._fullLayout) {
                const droneIndex=Number(String(observer).match(/^uav-(\d+)$/)?.[1]);
                const actual=rows.map(row => finite(row.heights_m?.[droneIndex]) ? row.heights_m[droneIndex] : null);
                const target=rows.map(row => row.altitude_relay_id === observer && finite(row.target_height_m) ? row.target_height_m : null);
                const traces=[{x:rows.map(row=>row.time_s),y:actual,type:'scatter',mode:'lines',name:name+' 실제 높이',line:{color:'#46679a',width:2.3},connectgaps:false,hovertemplate:'%{x:.1f}초 · %{y:.1f} m<extra>실제 높이</extra>'}];
                if (target.some(finite)) traces.push({x:rows.map(row=>row.time_s),y:target,type:'scatter',mode:'lines',name:'기록된 목표 높이',line:{color:'#9a80b8',width:1.7,dash:'dash',shape:'hv'},connectgaps:false,hovertemplate:'%{x:.1f}초 · %{y:.1f} m<extra>목표 높이</extra>'});
                traces.push({x:rows.map(row=>row.time_s),y:rows.map(row=>finite(row.network_margin_db)?row.network_margin_db:null),xaxis:'x2',yaxis:'y2',type:'scatter',mode:'lines',name:'전체 수색망 통신 여유',line:{color:'#4c7486',width:1.8,shape:'hv'},connectgaps:false,hovertemplate:'%{x:.1f}초 · %{y:.1f} dB<extra>통신 여유</extra>'});
                const axis={range:[0,Math.max(1,duration())],gridcolor:'#e5ecf1',zeroline:false};
                await Plot.react(targetPlot,traces,{height:340,autosize:true,paper_bgcolor:'#fff',plot_bgcolor:'#fcfdfe',margin:{l:60,r:18,t:34,b:44},
                    font:{family:'Arial, sans-serif',size:12,color:'#294b63'},legend:{orientation:'h',x:0,y:1.15,font:{size:11}},
                    dragmode:'zoom',hovermode:'closest',uirevision:expectedKey,shapes:plotShapes(time),
                    xaxis:{...axis,anchor:'y',showticklabels:false},xaxis2:{...axis,anchor:'y2',matches:'x',title:{text:'비행 시각 (초)',font:{size:12}}},
                    yaxis:{domain:[.48,1],title:{text:'높이 (m)',font:{size:12}},gridcolor:'#e5ecf1',fixedrange:true,zeroline:false},
                    yaxis2:{domain:[0,.31],title:{text:'여유 (dB)',font:{size:12}},gridcolor:'#e5ecf1',fixedrange:true,zeroline:false}},
                    {responsive:true,displayModeBar:false,scrollZoom:false,doubleClick:'reset'});
                if (ticket !== request || expectedKey !== key || disposed) {Plot.purge(targetPlot);plotKey='';return;}
                plotKey=figureKey;
                set('chart-title',name+'의 고도 · 전체 수색망의 통신 여유');
            } else {
                await Plot.relayout(targetPlot,{'shapes[0].x0':time,'shapes[0].x1':time});
                if (targetPlot !== plot) Plot.purge(targetPlot);
            }
        } catch (error) {
            if (expectedKey === key && !disposed) {
                plotKey='';
                set('chart-title','그래프를 확인할 수 없습니다. 수치와 운용 기록을 이용해 주세요.');
            }
        } finally {
            plotBusy=false;
            if (plotPending) queuePlot();
        }
    }
    async function changeRange(range) {
        if (!Plot || !plot._fullLayout || plotBusy || range[1] <= range[0]) return;
        plotBusy=true;
        try {await Plot.relayout(plot,{'xaxis.range':range,'xaxis2.range':range,'xaxis.autorange':false,'xaxis2.autorange':false});}
        finally {plotBusy=false;queuePlot();}
    }
    function bindPlotPointer() {
        plot.addEventListener('pointerdown',event => {pointer={x:event.clientX,y:event.clientY,id:event.pointerId};});
    }
    bindPlotPointer();
    function finishPointer(event) {
        const start=pointer;
        if (!start || start.id !== event.pointerId) return;
        pointer=null;
        if (event.type === 'pointerup' && Math.hypot(event.clientX-start.x,event.clientY-start.y) < 4 && state) {
            const axis=plot._fullLayout?.xaxis, box=plot.getBoundingClientRect(), x=event.clientX-box.left-(axis?._offset || 0);
            if (axis && x >= 0 && x <= axis._length) {
                const y=event.clientY-box.top, axes=[plot._fullLayout.yaxis,plot._fullLayout.yaxis2];
                if (axes.some(a=>y>=a._offset && y<=a._offset+a._length)) onSeek(Math.max(0,Math.min(duration(),Math.round(axis.p2d(x)*2)/2)));
            }
        }
        queuePlot();
    }
    globalThis.addEventListener('pointerup',finishPointer);
    globalThis.addEventListener('pointercancel',finishPointer);
    const resize=typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
        if (resizeFrame) cancelAnimationFrame(resizeFrame);
        resizeFrame=requestAnimationFrame(() => {
            if (!disposed && !host.hidden && el('chart-details').open && plot._fullLayout && !plotBusy && !pointer) Plot?.Plots?.resize(plot);
        });
    }) : null;
    resize?.observe(host);

    return {
        update(next) {
            if (disposed) return;
            if (!operationEligible(next?.manifest)) {if(key || !host.hidden)reset();return;}
            const nextKey=pinKey(next.manifest), changed=key !== nextKey;
            if (changed) {reset();key=nextKey;}
            state={...next, time:finite(next.time) ? next.time : 0};host.hidden=false;
            if (changed) load(next.manifest,nextKey);
            else draw();
        },
        reset,
        getRole(id) {const role=data?.roles?.find(item=>item.drone_id===id);return role?{...role,label:DUTIES[role.duty] || role.label}:null;},
        getReadout(time,observer) {return state ? operationReadout(data,{...state,time:finite(time)?time:state.time,observer:observer ?? state.observer}) : null;},
        snapshot() {return {visible:!host.hidden,status:data?.status || (key?'unavailable':'hidden'),key,readout:lastReadout,recordCount:records.length};},
        dispose() {reset();disposed=true;resize?.disconnect();if(resizeFrame)cancelAnimationFrame(resizeFrame);globalThis.removeEventListener('pointerup',finishPointer);globalThis.removeEventListener('pointercancel',finishPointer);},
    };
}
