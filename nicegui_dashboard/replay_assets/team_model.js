import {esc, fmt, num} from './render.js';
import {mapName} from './library.js';
import {operationIdentityMatches} from './operations.js';

// Explain only the deployment notes verified for this flight's immutable pin.
// A routine code update keeps the presentation, while changed or incomplete
// documentation cannot silently inherit the preceding release's blend values.
export function modelExplanation(manifest, profile) {
    const pin=profile?.identity;
    const notes=(profile?.notes || []).join('\n');
    const matched=profile?.status === 'verified' && pin?.commit === manifest.team_commit &&
        pin?.checkpoint === manifest.model_sha256 && pin?.controller === manifest.controller &&
        pin?.interval === manifest.decision_interval_s;
    if(!matched || manifest.controller !== 'halfsec_planned_eight' ||
        !/3초\s*이상\s*7초\s*미만/.test(notes) || !/PPO/.test(notes) ||
        !/고정/.test(notes) || !/고도/.test(notes) || !/하강/.test(notes) || !/복귀/.test(notes)) return null;
    const patterns=[/Transformer[^\n%]*?(\d+(?:\.\d+)?)\s*%/,/LightGBM\s*(\d+(?:\.\d+)?)\s*%/,/물리\s*점수\s*(\d+(?:\.\d+)?)\s*%/];
    const found=patterns.map(pattern=>notes.match(pattern)?.[1]);
    if(found.some(value=>value == null))return null;
    const weights=found.map(Number);
    if(weights.some(value=>!Number.isFinite(value)||value<0||value>100) || Math.abs(weights.reduce((a,b)=>a+b,0)-100) > 1e-6)return null;
    return {weights,source:'pinned_release_notes'};
}

export function hasVerifiedModelExplanation(manifest, profile) {
    return Boolean(modelExplanation(manifest,profile));
}

function regionName(manifest) {
    const label=manifest.region_name || manifest.display_name || mapName(manifest);
    return label && label !== manifest.region ? label : '선택 지역';
}

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

function heading(title, badge, source = '배포본에 포함된 평가') {
    return '<div class="summary-heading"><div><span class="tag muted">' + esc(source) + '</span><h4>' +
        esc(title) + '</h4></div><span class="tag muted">' + esc(badge) + '</span></div>';
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

function regionalCards(regional, manifest) {
    const control = regional.control || {}, forecast = regional.forecast || {};
    const missing = regional.missing || [];
    const region=esc(regionName(manifest));
    return (regional.status === 'error' ? '<div class="notice">' + esc(regional.message) + '</div>' : '') +
        '<div class="summary-grid" data-regional-model="'+esc(manifest.region)+'">' +
        '<article class="summary-card final-summary">' +
        heading(regionName(manifest)+' · 제어 성능', '적용 비행 ' + num(control.flights ?? 1, 0) + '회', '현재 지역의 평가') +
        '<p class="summary-description">선택한 예측·재배치 적용 비행의 결과입니다. 전체 지역의 종합 성능과 구분합니다.</p>' +
        metrics(control, controlMetrics) +
        '<p class="hint">단절 횟수·시간은 비행 1시간 기준입니다. 이동 거리는 중계기의 수평 이동거리 합계를 비행별로 평균한 값이며, 여기서는 선택한 1회 비행의 합계입니다. 고도 이동은 포함하지 않습니다.</p>' +
        '<p class="hint">0.25초 내 전달률은 기한 안에 도착한 비율이며, 메인 카드의 일반 PDR과 구분합니다.</p></article>' +
        '<article class="summary-card final-summary">' +
        heading(regionName(manifest)+' · 선행 탐지 성능', '평가 단절 ' + num(forecast.events, 0) + '건', '같은 모델 · 별도 평가 비행') +
        '<p class="summary-description">'+region+'의 같은 지도·조건에서 수행한 별도 기준 비행에 현재 모델을 평가한 결과입니다. 재생 중인 적용 비행의 단절 횟수와 다를 수 있습니다.</p>' +
        metrics(forecast, forecastMetrics) +
        '<p class="hint">선행 탐지율은 실제 단절 중 미리 경보한 비율, 경보 정밀도는 경보 중 적중한 비율입니다. AP는 경보 기준을 바꿔 가며 예측 점수의 구분 능력을 평가한 값입니다. 재생 시각의 단절 확률과는 다릅니다.</p>' +
        (forecast.events === 0 ? '<p class="hint">평가 단절이 0건이면 탐지율만으로 예측 성능을 판단할 수 없습니다.</p>' : '') +
        (forecast.ranking_rows === 0 ? '<p class="hint">AP 평가에 사용할 유효한 관측 시점이 없어 AP를 산출하지 않습니다.</p>' : '') +
        (forecast.ranking_rows > 0 && forecast.ranking_positives === 0 ? '<p class="hint">선행 단절에 해당하는 AP 평가 시점이 0개입니다. AP 0은 이 평가 범위와 함께 해석하세요.</p>' : '') +
        '</article></div>' +
        (missing.length ? '<p class="hint" data-model-missing>추가 기록이 필요한 항목: ' +
            missing.map(key => esc([...controlMetrics, ...forecastMetrics].find(row => row[0] === key.split('.').at(-1))?.[1] || key)).join(' · ') + '</p>' : '');
}

function regionalSources(regional, manifest) {
    if (!regional) return '';
    const sources = regional.provenance || {};
    const evaluation = sources.forecast || {}, scores = regional.forecast || {};
    return '<p>지역 평가 범위: '+esc(regionName(manifest))+' · 선택한 적용 비행 1회 / 별도 예측 평가 기준 비행 1회</p>' +
        '<p>연구 코드의 제어 집계 함수와 예측 평가 함수를 그대로 사용합니다. 보고서 전체 실험 집계와 합치지 않습니다.</p>' +
        '<p>평가 모델: ' + num(evaluation.evaluated_step, 0) + '회차 · 평가 비행 시간 ' + num(evaluation.duration_s, 1) + '초<br>' +
        'AP 평가 시점 ' + num(scores.ranking_rows, 0) + '개 · 선행 단절 구간에 해당하는 시점 ' + num(scores.ranking_positives, 0) + '개</p>' +
        '<p>제어 집계: ' + esc([sources.control?.module, sources.control?.function].filter(Boolean).join('.')) + '<br>' +
        '예측 평가: ' + esc([evaluation.ap_evaluation?.sources?.evaluate?.module,
            evaluation.ap_evaluation?.sources?.evaluate?.function].filter(Boolean).join('.')) + '</p>';
}

function placementSummary(manifest, operations) {
    if (operations?.status !== 'available' || !operationIdentityMatches(operations,manifest) || !Array.isArray(operations.roles))
        return '<p class="model-placement-note">고정 중계와 고도 조정의 실제 채택 여부는 확인된 비행 운용 기록에서 표시합니다.</p>';
    const fleet=manifest.fleet || [];
    const name=id=>{
        const node=fleet.find(item=>item.id===id);
        if(!node)return id;
        return (node.role==='search'?'S':'R')+(fleet.filter(item=>item.role===node.role).findIndex(item=>item.id===id)+1);
    };
    const groups=[['fixed_relay','고정 중계'],['altitude_relay','고도 조정 중계']];
    const badges=groups.map(([duty,label])=>{
        const ids=operations.roles.filter(role=>role.duty===duty).map(role=>name(role.drone_id));
        return '<span data-model-duty="'+duty+'" data-adopted="'+Boolean(ids.length)+'">'+label+' <b>'+(ids.length?esc(ids.join('·')):'미채택')+'</b></span>';
    }).join('');
    return '<div class="model-placement"><span>이번 비행에 채택한 배치</span><div>'+badges+'</div></div>';
}

function modelOverview(manifest, profile, nominal, known, operations) {
    const model=profile?.model || {}, fleet=manifest.fleet || [];
    const facts=[['재배치 판단 주기',num(manifest.decision_interval_s)+'초']];
    if (model.step != null) facts.push(['사용한 학습 결과',num(model.step,0)+'회차']);
    if (fleet.length) facts.push(['비행 편대','수색 '+fleet.filter(node=>node.role==='search').length+'대 · 중계 '+fleet.filter(node=>node.role==='relay').length+'대']);
    return '<section class="model-overview"><div class="model-heading"><div><span class="model-eyebrow">'+esc(regionName(manifest))+' · 선택한 비행의 모델</span><h3>'+
        (known?'단절 위험을 예측하고, 안전한 이동 후보를 선택합니다':'현재 비행의 모델 구성과 평가')+'</h3></div><span class="model-tag">'+(nominal?'비교하는 적용 모델':'현재 적용 모델')+'</span></div>'+
        (nominal?'<p class="model-baseline-note">기본 대형으로 비행 중입니다. 아래는 짝지어 비교하는 모델 적용 비행의 구성입니다.</p>':'')+
        '<p class="model-lead">'+(known?'통신 관측의 변화와 예상 전파 상태를 함께 살피고, 검사를 통과한 수평 이동 후보 중에서 PPO가 행동을 고릅니다. 고정 배치와 고도 조정은 필요에 따라 별도 절차로 운영합니다.':'선택한 비행의 코드와 모델에 연결된 기록입니다. 세부 작동 설명은 확인된 배포 기록을 기준으로 표시합니다.')+'</p>'+
        '<dl class="model-facts">'+facts.map(([label,value])=>'<div><dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd></div>').join('')+'</dl>'+placementSummary(manifest,operations)+'</section>';
}

function predictionOverview(explanation) {
    const weights=explanation.weights;
    const components=[
        ['transformer','Transformer','링크 단절 시점과 전체망 위험 예측','최근 약 20초의 통신 관측으로 드론 쌍의 향후 단절 시점을 예측합니다. 링크 정보와 예정 경로를 결합해 전체 수색망의 선행 위험도 추정합니다.'],
        ['lightgbm','LightGBM','선행 위험과 임박 위험 추정','경로·차폐·통신 상태 등의 특징으로 3~7초의 선행 위험을 계산합니다. 3초 이내 임박 위험은 별도로 학습한 모델이 평가합니다.'],
        ['propagation','전파 계산','예상 경로의 통신 여유 평가','예상 위치의 거리·고도·건물 차폐·간섭을 반영해 통신 여유를 계산합니다. 위험 점수와 수평 이동 후보의 안전 검사에 사용합니다.'],
    ];
    return '<section class="model-explainer" aria-label="예측을 구성하는 세 가지 정보"><div class="model-section-head"><h4>단절 위험을 살피는 세 가지 정보</h4><span>선택한 비행의 배포 설명 기준</span></div><div class="model-components">'+
        components.map(([key,name,label,description])=>'<article class="model-component" data-model-component="'+key+'"><span class="model-component-name">'+name+'</span><h5>'+label+'</h5><p>'+description+'</p></article>').join('')+'</div>'+
        '<div class="model-blend" data-model-blend="'+weights.join('-')+'"><div class="model-section-head"><h4>선행 예방 판단에 사용하는 혼합 점수</h4><span>3초 이상 ~ 7초 미만 뒤의 새 지속 단절</span></div>'+
        '<div class="model-blend-bar" role="img" aria-label="Transformer 기반 전체망 점수 '+weights[0]+'%, LightGBM '+weights[1]+'%, 물리 점수 '+weights[2]+'%"><span class="blend-transformer" style="flex:'+weights[0]+'">'+weights[0]+'%</span><span class="blend-lightgbm" style="flex:'+weights[1]+'">'+weights[1]+'%</span><span class="blend-propagation" style="flex:'+weights[2]+'">'+weights[2]+'%</span></div>'+
        '<div class="model-blend-key"><span><i class="blend-transformer"></i>Transformer 기반 전체망 점수 '+weights[0]+'%</span><span><i class="blend-lightgbm"></i>LightGBM '+weights[1]+'%</span><span><i class="blend-propagation"></i>물리 점수 '+weights[2]+'%</span></div>'+
        '<p>편대 예측에 사용할 관측이 있을 때 적용하는 비율입니다. 관측된 드론 쌍이 없으면 기존 예방 점수를 사용하므로 모든 시각에 이 비율이 적용되는 것은 아닙니다.</p></div>'+
        '<div class="model-reading"><div><strong>지도에서 보는 ‘다음 단절 예측’</strong><p>Transformer 기반 편대 모델이 낸 추정 확률입니다. 현재 판단 시각을 기준으로 3초 이상 7초 미만 뒤에 1초 이상 지속할 새 단절이 시작될 가능성을 보여 줍니다.</p></div><div><strong>재배치에 사용하는 예방 판단 점수</strong><p>예측과 물리 정보를 혼합한 판단용 점수입니다. 기준을 넘더라도 이동 후보·안전 조건을 검토하므로 곧바로 이동 명령이 나오는 것은 아닙니다.</p></div></div>'+
        '<p class="model-note">지도 확률은 직전 수색망이 연결돼 있고 관측이 유효하며 예측 시간 범위를 확보한 시각에 표시합니다. 이미 단절 중이거나 시작·종료 경계 등 조건이 맞지 않으면 새 단절 예측값으로 표시하지 않습니다.</p>'+
        '<p class="model-note">지도 확률, 혼합 판단 점수, 아래의 선행 탐지율은 서로 다른 값입니다. 선행 탐지율은 별도 기준 비행에서 실제 발생한 단절을 얼마나 미리 찾았는지 평가합니다.</p></section>';
}

function movementOverview() {
    return '<section class="model-explainer" aria-label="수평 재배치와 배치 고도 절차"><div class="model-section-head"><h4>모델과 별도 절차가 맡는 역할</h4><span>해당 배포 구성에서 지원하는 기능 · 실제 채택 여부는 위 배치 기록</span></div><div class="model-decisions">'+
        '<article data-model-action="ppo"><span class="model-component-name">PPO · 수평 재배치</span><h5>검사를 통과한 이동 후보 중 선택</h5><p>수색 진행과 통로, 임박한 단절 위험 등을 검사한 후보를 평가해 유지하거나 수평 보정을 요청합니다. 선택·명령 발행·수신·실제 이동은 각각의 기록으로 확인합니다.</p></article>'+
        '<article data-model-action="placement-altitude"><span class="model-component-name">별도 배치·고도 절차</span><h5>고정 중계 위치와 구간별 높이 운영</h5><p>비행 전에 고정 위치와 고도 조정 구간을 검토합니다. 비행 중에는 상승 → 통신 확인 → 통과 → 단계적 하강을 진행하며, 하강 후 확인 조건을 충족하지 못하면 이전 높이로 복원합니다.</p></article></div>'+
        '<p class="model-note">PPO가 모든 기체의 위치와 고도를 직접 정하는 구조는 아닙니다. 실제 적용 역할과 고도 단계는 지도 아래 ‘배치와 비행 운용’에서 확인하세요.</p></section>';
}

export function renderTeamModel(manifest, profile, nominal = false, operations = null) {
    manifest={...manifest,region_name:manifest.region_name || profile?.regional?.scope?.label || profile?.regional?.scope?.region_label};
    const model = profile?.model || {};
    const sources = Object.entries(profile?.source_refs || {});
    const notes = profile?.notes || [];
    const regional = profile?.regional;
    const explanation=modelExplanation(manifest,profile), known=Boolean(explanation);
    return '<div class="team-model" data-model-explanation="'+(known?'verified':'recorded')+'">'+
        modelOverview(manifest,profile,nominal,known,operations)+(known?predictionOverview(explanation)+movementOverview():
            '<section class="model-explainer"><h4>확인된 배포 설명</h4>'+
            (profile?.release_note?'<p>'+esc(profile.release_note)+'</p>':'<p>이 코드와 모델의 세부 작동 설명은 아직 확인되지 않았습니다. 확인된 평가값과 출처를 아래에 표시합니다.</p>')+
            (notes.length?'<details class="model-recorded-notes"><summary>배포본에 기록된 예측·재배치 설명</summary><ul class="model-release-notes">'+notes.map(note=>'<li>'+esc(note)+'</li>').join('')+'</ul></details>':'')+'</section>')+
        '<div class="model-section-head model-metrics-title"><h4>'+(regional?esc(regionName(manifest))+'에서 확인한 성능':'배포 자료에서 확인한 성능')+'</h4><span>제어 결과 4개 · 예측 평가 4개</span></div>'+
        (regional ? regionalCards(regional,manifest) : '<div class="summary-grid">' + controlCard(profile) + forecastCard(profile) + '</div>') +
        '<details class="summary-provenance model-provenance"><summary>기술 설정·평가 범위·출처 확인</summary><p>제어기 '+esc(manifest.controller || manifest.model_id)+
        (model.threshold != null ? '<br>예방 판단 임계값 '+num(model.threshold,4) : '')+'</p><p>팀 코드 ' +
        esc(manifest.team_commit) + '<br>모델 파일 SHA-256 ' + esc(manifest.model_sha256) + '</p>' +
        (profile?.completed_at ? '<p>제어 평가 기록 시각 ' + esc(profile.completed_at) + '</p>' : '') +
        (profile?.forecast?.completed_at ? '<p>선행 탐지 평가 기록 시각 ' + esc(profile.forecast.completed_at) + '</p>' : '') +
        regionalSources(regional,manifest) + (known && notes.length?'<h5>해당 배포본에 기록된 설명</h5><ul>'+notes.map(note=>'<li>'+esc(note)+'</li>').join('')+'</ul>':'')+
        (sources.length ? '<ul>' + sources.map(([path, sha]) => '<li>' + esc(path) +
            '<br><small>SHA-256 ' + esc(sha) + '</small></li>').join('') + '</ul>' : '') +
        '</details></div>';
}

export async function teamModel(manifest, target, isCurrent, nominal = false) {
    if (!manifest) return;
    const run = manifest.run_id, revision = manifest.bundle_revision;
    target.innerHTML = '<div class="empty-state">현재 모델 정보를 불러옵니다…</div>';
    try {
        const base='/part2/team/' + encodeURIComponent(run) + '/' + encodeURIComponent(revision);
        const placement=manifest.controller === 'halfsec_planned_eight' ? fetch(base+'/operations')
            .then(response=>response.ok?response.json():null).catch(()=>null) : Promise.resolve(null);
        const result = await fetch(base + '/model');
        if (!result.ok) throw new Error('배포 자료를 불러올 수 없습니다.');
        const data = await result.json();
        if (data.run_id !== run || data.revision !== revision || data.identity?.commit !== manifest.team_commit ||
            data.identity.checkpoint !== manifest.model_sha256 || data.identity.controller !== manifest.controller ||
            data.identity.interval !== manifest.decision_interval_s) throw new Error('선택한 비행과 모델 정보가 다릅니다.');
        const operations=await placement;
        if (isCurrent()) target.innerHTML = renderTeamModel(manifest, data.profile, nominal, operations);
    } catch (error) {
        if (isCurrent()) target.innerHTML = '<div class="notice">' + esc(error.message) + '</div>' +
            renderTeamModel(manifest, null, nominal);
    }
}
