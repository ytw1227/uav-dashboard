// Only static presentation geometry is shared between flights. Every time series is pinned.
import {marginRecords} from './margin_data.js';

const pinId = /^[a-zA-Z0-9_-]+$/;
const fields = row => ({...row, ...row?.metadata});
const revisionOf = row => row?.bundle_revision || row?.revision;
let archiveRows, active, archivedAPI, archivedChunk;

async function json(path, query='') {
    const response = await fetch(path + query);
    if (!response.ok) throw new Error('팀 비행 자료를 읽을 수 없습니다 (' + response.status + ')');
    return response.json();
}

function root(id, revision) {
    if (!pinId.test(id) || !pinId.test(revision)) throw new Error('잘못된 팀 비행 식별자입니다.');
    return '/part2/team/' + id + '/' + revision;
}

export function installTeamReader(catalog) {
    archiveRows = catalog.runs;
    const portable = window.__PORTABLE__;
    archivedAPI = portable.api;
    archivedChunk = window.__DASHBOARD_READ_CHUNK__;
    window.__PORTABLE__ = {...portable, api(url, options) {
        const path = new URL(url, location.origin);
        const match = path.pathname.match(/^\/api\/runs\/(team-[\w-]+)\/(manifest|events|timeline|scene)$/);
        if (!match) return archivedAPI(url, options);
        const [, id, part] = match;
        const revision = path.searchParams.get('revision');
        if (active?.row.run_id !== id || revisionOf(active.row) !== revision)
            return Promise.reject(new Error('다른 팀 비행으로 이동했습니다.'));
        const base = root(id, revision);
        if (part === 'manifest') return Promise.resolve(active.manifest);
        if (part === 'timeline') {
            return path.searchParams.get('channels') === 'observations'
                ? json(base + '/observations', path.search) : json(base + '/overview');
        }
        return json(base + '/' + part, path.search);
    }};
    window.__DASHBOARD_READ_CHUNK__ = id => {
        const match = String(id).match(/^team-(book|analysis)\/([\w-]+)\/([\w-]+)$/);
        return match ? json(root(match[2],match[3]) + '/incident/' + match[1]) : archivedChunk(id);
    };
}

export async function teamDescriptor(region, id, revision, sha) {
    const record = await json(root(id,revision) + '/descriptor');
    for (const item of [record.manifest, record.row]) {
        const data = fields(item);
        if (item?.run_id !== id || revisionOf(item) !== revision || data.team_commit !== sha ||
            data.region !== region || data.mode !== 'v21' || !data.map_hash)
            throw new Error('선택한 지역·팀 버전과 저장된 비행이 일치하지 않습니다.');
    }
    const pair = window.__NICEGUI_CATALOG__.scope.flight_pairs[region];
    const archive = archiveRows.find(row => row.run_id === pair?.v21);
    const reference = window.__DASHBOARD_ANALYSIS__.incidents[archive?.run_id];
    const visual = window.__UAV_VISUAL__.maps[region];
    // scene_sha256 identifies the converted mesh. map_hash identifies its original scene file.
    if (!reference || reference.revision !== revisionOf(archive) || !visual?.map_chunk ||
        reference.map_chunk !== visual.map_chunk || fields(archive).map_hash !== record.manifest.map_hash)
        throw new Error('팀 비행의 지도 원본과 3D 건물 지도의 출처가 다릅니다.');
    record.nominal = archiveRows.find(row => row.run_id === pair.nominal);
    if (fields(record.nominal).map_hash !== record.manifest.map_hash) record.nominal = null;
    record.incidents = {...record.incidents, map_chunk:reference.map_chunk,
        signal_template_chunk:reference.signal_template_chunk};
    return record;
}

export function setTeamFlight(record) {
    if (active) {
        delete window.__DASHBOARD_ANALYSIS__.incidents[active.row.run_id];
        delete marginRecords[active.row.run_id];
    }
    active = record;
    if (record) {
        window.__DASHBOARD_ANALYSIS__.incidents[record.row.run_id] = record.incidents;
        if (record.margin?.revision === revisionOf(record.row)) marginRecords[record.row.run_id] = record.margin;
    }
    return record ? [...archiveRows, record.row] : archiveRows;
}

export function teamPair(row) {
    return active && (row?.run_id === active.row.run_id || row?.run_id === active.nominal?.run_id)
        ? {v21:active.row, nominal:active.nominal} : null;
}

export function teamContext() { return active?.row?.run_id || null; }
export function teamManifest() { return active?.manifest || null; }
