import {esc, fmt, num} from './render.js';

const controlMetrics = [
    ['starts_per_h', '시간당 새 지속 단절', '회 / 시간'],
    ['cut_seconds_per_h', '시간당 지속 단절 시간', '초 / 시간'],
    ['timely_pdr', '0.25초 내 전달률', 'ratio'],
    ['relay_m', '평균 중계기 이동 거리', 'm'],
];
const forecastMetrics = [
    ['recall', '선행 탐지율', 'ratio'],
    ['precision', '경보 정밀도', 'ratio'],
    ['false_per_h', '오경보·늦은 경보', '회 / 편대·시간'],
    ['average_precision', '평균 정밀도 (AP)', 'ratio'],
];

function metrics(values, definitions) {
    return '<div class="summary-metrics">' + definitions.map(([key, label, unit]) =>
        '<div class="summary-value" data-metric="' + key + '"><span>' + label +
        '</span><strong>' + fmt(values[key], unit, 2) + '</strong></div>').join('') + '</div>';
}

function heading(title, badge) {
    return '<div class="summary-heading"><div><span class="tag muted">배포본에 포함된 평가</span><h4>' +
        title + '</h4></div><span class="tag muted">' + esc(badge) + '</span></div>';
}

function scope(group) {
    return '지도 ' + num(group.maps, 0) + '곳 · 임무 완료 ' +
        num(group.completed, 0) + ' / ' + num(group.flights, 0) + '개 비행';
}

function controlCard(profile) {
    const group = profile?.controls?.separate_maps;
    const stalls = profile?.controls?.known_stalls;
    return '<article class="summary-card final-summary">' +
        heading('제어 성능', group ? num(group.flights, 0) + '개 비행' : '평가 미확인') +
        '<p class="summary-description">' + (group ? '별도 지도 검증 · ' + scope(group) :
            esc(profile?.control_reason || '이 팀 버전에서 해당 모델의 제어 평가 기록을 확인할 수 없습니다.')) +
        '</p>' + metrics(group || {}, controlMetrics) +
        '<p class="hint">지역별 기본 대형과 모델 적용 비행은 ‘실험 비교’에서 확인하세요. 0.25초 내 전달률은 생성 패킷 중 기한 안에 도착한 비율입니다.</p>' +
        (stalls ? '<details><summary>기존 미완주 조건의 재검증</summary><p>' + scope(stalls) +
            '</p>' + metrics(stalls, controlMetrics) + '</details>' : '') + '</article>';
}

function forecastCard(profile) {
    const scores = profile?.forecast || {};
    const available = Object.hasOwn(scores, 'recall');
    return '<article class="summary-card final-summary">' +
        heading('선행 탐지 성능', available ? '단절 ' + num(scores.events, 0) + '건' : '배포 평가 미확인') +
        '<p class="summary-description">' + (available ? '배포본 기준 비행 · 운용 임계값에서의 예측 평가' :
            esc(profile?.forecast_reason || '이 비행과 같은 모델 가중치로 측정한 배포 평가 기록을 확인할 수 없습니다.')) +
        '</p>' + metrics(scores, forecastMetrics) +
        '<p class="hint">선행 탐지율은 평가 비행에서 실제로 발생한 단절 중 미리 경보한 비율입니다. 지도 재생 시각의 ‘다음 단절 예측’은 그 시각의 모델 추정값입니다.</p></article>';
}

export function renderTeamModel(manifest, profile, nominal = false) {
    const model = profile?.model || {};
    const headingText = nominal ? '같은 지역의 예측·재배치 적용 모델' : '현재 비행에 사용한 모델';
    const sources = Object.entries(profile?.source_refs || {});
    const notes = profile?.notes || [];
    return '<article class="summary-card final-summary"><div class="summary-heading"><div>' +
        '<span class="tag teal">현재 적용 구성</span><h4>' + headingText + '</h4></div></div>' +
        (nominal ? '<p>기본 대형으로 비행 중입니다. 아래는 짝지어 비교하는 모델 적용 비행의 구성입니다.</p>' : '') +
        '<p>예측·제어 <strong>' + esc(manifest.controller || manifest.model_id) + '</strong><br>재배치 판단 주기 <strong>' +
        num(manifest.decision_interval_s) + '초</strong> · 팀 코드 <strong>' +
        esc(manifest.team_commit?.slice(0, 7)) + '</strong>' +
        (model.step != null ? '<br>학습 체크포인트 ' + num(model.step, 0) + '회차' : '') +
        (model.threshold != null ? ' · 판단 임계값 ' + num(model.threshold, 4) : '') + '</p>' +
        (profile?.release_note ? '<p class="summary-description">' + esc(profile.release_note) + '</p>' : '') +
        (notes.length ? '<details><summary>예측과 재배치 방식</summary><ul>' +
            notes.map(note => '<li>' + esc(note) + '</li>').join('') + '</ul></details>' : '') +
        '</article><div class="summary-grid">' + controlCard(profile) + forecastCard(profile) +
        '</div><details class="summary-provenance"><summary>모델과 평가 출처</summary><p>팀 코드 ' +
        esc(manifest.team_commit) + '<br>모델 파일 SHA-256 ' + esc(manifest.model_sha256) + '</p>' +
        (profile?.completed_at ? '<p>제어 평가 기록 시각 ' + esc(profile.completed_at) + '</p>' : '') +
        (profile?.forecast?.completed_at ? '<p>선행 탐지 평가 기록 시각 ' + esc(profile.forecast.completed_at) + '</p>' : '') +
        (sources.length ? '<ul>' + sources.map(([path, sha]) => '<li>' + esc(path) +
            '<br><small>SHA-256 ' + esc(sha) + '</small></li>').join('') + '</ul>' : '') +
        '</details>';
}

export async function teamModel(manifest, target, isCurrent, nominal = false) {
    if (!manifest) return;
    const run = manifest.run_id, revision = manifest.bundle_revision;
    target.innerHTML = '<div class="empty-state">현재 모델 정보를 불러옵니다…</div>';
    try {
        const result = await fetch('/part2/team/' + encodeURIComponent(run) + '/' + encodeURIComponent(revision) + '/model');
        if (!result.ok) throw new Error('배포 자료를 불러올 수 없습니다.');
        const data = await result.json();
        if (data.run_id !== run || data.revision !== revision || data.identity?.commit !== manifest.team_commit ||
            data.identity.checkpoint !== manifest.model_sha256) throw new Error('선택한 비행과 모델 정보가 다릅니다.');
        if (isCurrent()) target.innerHTML = renderTeamModel(manifest, data.profile, nominal);
    } catch (error) {
        if (isCurrent()) target.innerHTML = '<div class="notice">' + esc(error.message) + '</div>' +
            renderTeamModel(manifest, null, nominal);
    }
}
