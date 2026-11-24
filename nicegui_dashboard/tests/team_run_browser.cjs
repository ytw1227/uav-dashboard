// Invoked by the temporary local service in team_run_browser.py.
const assert = require('node:assert/strict');
const {chromium} = require('playwright-core');

async function checkTeamReplay(page) {
  await page.waitForFunction(() => {
    const state = window.uavReplay?.snapshot();
    return state?.run_id === 'team-synthetic-browser-flight' && !state.loading &&
      state.revision === 'synthetic_revision_0001' && window.__UAV_MAP_TEST__?.snapshot()?.ready;
  });
  const cards = page.locator('#metricCards .metric-value');
  assert.equal(await cards.count(), 4);
  assert.match(await cards.nth(0).innerText(), /83\.33\s*%/);
  assert.match(await cards.nth(1).innerText(), /75(?:\.0+)?\s*%/);
  assert.match(await cards.nth(2).innerText(), /1\s*회/);
  assert.equal(await page.locator('canvas[aria-label^="보관 비행 지도"]:visible').count(), 0);
}

async function checkTeamComparison(page) {
  await page.locator('button[data-uav-tab="compare"]').click();
  const table = page.locator('#pairedTable');
  const cell = (key,arm) => table.locator(`tr[data-metric="${key}"] td[data-arm="${arm}"]`);
  await table.locator('tbody tr').first().waitFor();
  assert.equal(await cell('pdr','nominal').innerText(), '—');
  assert.equal(await cell('pdr','v21').innerText(), '75%');
  const write = await page.request.post(new URL('/qa/complete-comparison', page.url()).href);
  assert.equal(write.ok(), true);
  await cell('pdr','nominal').filter({hasText:'70%'}).waitFor();
  assert.equal(await table.locator('tbody tr').count(), 9);
  assert.deepEqual(await table.locator('thead th').allTextContents(), ['지표','기본 대형','모델 적용','차이 (적용 − 기본)']);
  assert.equal(await cell('pdr','delta').innerText(), '+5%p');
  assert.equal(await cell('mean_recovery_s','nominal').innerText(), '3 s');
  assert.equal(await cell('unrecovered_at_end','nominal').innerText(), '1건');
  assert.equal(await cell('unrecovered_at_end','v21').innerText(), '0건');
  assert.equal(await cell('persistent_onsets','delta').innerText(), '-1회');
  assert.equal(await cell('persistent_cut_seconds','delta').innerText(), '-3 s');
  assert.equal(await cell('information_shared_fraction','delta').innerText(), '0%p');
  assert.equal(await cell('duration_s','delta').innerText(), '-3 s');
  assert.equal(await cell('mission_completed','nominal').innerText(), '완료');

  // Finish an old team response after entering the archived comparison. Neither its data nor its
  // request status should replace the currently selected two flights.
  const endpoint = '**/part2/team/team-synthetic-browser-flight/synthetic_revision_0001/comparison';
  let held;
  await page.route(endpoint, route => {held=route;});
  await page.locator('button[data-uav-tab="map"]').click();
  const pending = page.waitForRequest(req => req.url().endsWith('/synthetic_revision_0001/comparison'));
  await page.locator('button[data-uav-tab="compare"]').click();
  await pending;
  await page.getByTestId('team-result-choice').click();
  await page.getByRole('option', {name:'기존 대시보드 비행'}).click();
  await page.waitForFunction(() => !window.uavReplay.snapshot().team_context);
  await page.locator('#pairedTable tbody tr').first().waitFor();
  const saved = await table.innerText();
  const delivered = page.waitForResponse(resp => resp.url().endsWith('/synthetic_revision_0001/comparison'));
  await held.fulfill({json:{status:'error',message:'늦게 도착한 팀 결과'}});
  await delivered;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  assert.equal(await table.innerText(), saved);
  assert.doesNotMatch(await page.locator('#pairedConditions').innerText(), /팀 코드|늦게 도착한 팀 결과/);
  await page.unroute(endpoint);
  await page.getByTestId('team-result-choice').click();
  await page.getByRole('option', {name:/팀 aaaaaaa/}).click();
  await cell('pdr','nominal').filter({hasText:'70%'}).waitFor();
}

async function main() {
  const browser = await chromium.launch({
    ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {channel: 'chrome'}),
    headless: true, args: ['--enable-unsafe-swiftshader'],
  });
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 900}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.setDefaultTimeout(30_000);
    await page.goto(process.argv[2]);
    const start = page.getByTestId('team-run-start');
    await page.waitForFunction(() => window.uavReplay?.snapshot().region === 'bundang');
    assert.equal(await start.count(), 1);
    assert.equal(await start.innerText(), '분당 새 비행 실행');
    await page.getByTestId('library-mode').click();
    await page.getByRole('option', {name:'기본 · 미적용'}).click();
    await page.waitForFunction(() => window.uavReplay?.snapshot().mode === 'nominal');
    await start.click();
    await checkTeamReplay(page);
    assert.match(await page.getByTestId('team-result-source').innerText(), /aaaaaaa.*0.5초/);
    assert.equal(new URL(page.url()).pathname, '/regions');
    const archive = page.locator('#uavReplaySurface');
    assert.equal(await archive.isVisible(), true);
    await page.locator('#playButton').click();
    await page.waitForFunction(() => window.uavReplay.snapshot().time > 0);
    if (await page.evaluate(() => window.uavReplay.snapshot().playing)) {
      await page.locator('#playButton').click();
    }
    const clock = page.locator('#timeInput');
    await clock.fill('1');
    await clock.press('Enter');
    await page.waitForFunction(() => document.querySelector('#fmFleetProbability')?.textContent === '모델 추정 71.8%');
    assert.equal(await page.locator('#fmForecast h4').innerText(), '새 수색망 단절 예측');
    assert.equal(await page.locator('.pe-risk-probability').innerText(), '모델 추정 71.8%');
    assert.equal(await page.locator('.pe-risk-probability').evaluate(n=>n.previousElementSibling.textContent),'다음 단절 예측');
    assert.match(await page.locator('#fmForecastWindow').textContent(), /4초 이상 8초 미만/);
    await clock.fill('1.5');
    await clock.press('Enter');
    await page.waitForFunction(() => document.querySelector('#fmScore')?.textContent === '확률 미산출');
    assert.equal(await page.locator('.pe-risk-probability').innerText(), '확률 미산출');
    await clock.fill('3.5');
    await clock.press('Enter');
    await page.waitForFunction(() => window.uavReplay.snapshot().time === 3.5 ||
                                      window.__PLAYBACK_TEST__?.snapshot().time === 3.5);
    assert.match(await page.locator('#controlTraceDetail').textContent(), /재배치 판단 점수/);
    assert.match(await page.locator('#controlTraceDetail').textContent(), /혼합 점수 0\.2/);
    await page.waitForFunction(() => document.querySelector('#fmScore')?.textContent === '현재 수색망 단절');
    assert.equal(await page.locator('.pe-risk-probability').innerText(), '새 단절 예측 대상 아님');
    await page.locator('#fmEvaluationTab').click();
    await page.locator('#fmEvaluationView').filter({hasText:/이 팀 버전의 기준 비행으로 별도 평가한 기록이 없어/}).waitFor();
    await page.locator('#fmFleet').click();
    const incident = 'persistent_search_outage-000000';
    await page.locator('#incidentQuickSelect').selectOption(incident);
    await page.locator('#incidentOpen').click();
    const dialog = page.locator('#incidentDialog[open][data-ready="true"]');
    await dialog.waitFor();
    assert.deepEqual(await dialog.evaluate(node => [node.dataset.event, +node.dataset.start, +node.dataset.end]),
                     [incident, 2, 4]);
    await page.keyboard.press('Escape');
    await page.locator('#incidentDialog[open]').waitFor({state:'hidden'});
    await checkTeamComparison(page);
    await page.locator('button[data-uav-tab="performance"]').click();
    await page.getByText('현재 비행에 사용한 모델', {exact:true}).waitFor();
    assert.match(await page.locator('#performanceContent').innerText(), /현재 비행에 사용한 모델/);
    assert.match(await page.locator('#performanceContent').innerText(), /0\.5초/);
    assert.match(await page.locator('#performanceContent').innerText(), /synthetic-halfsec-policy/);
    assert.match(await page.locator('#performanceContent').innerText(), /제어 성능/);
    assert.match(await page.locator('#performanceContent').innerText(), /선행 탐지 성능/);
    await page.locator('button[data-uav-tab="map"]').click();
    await page.locator('#flightNominal').click();
    await page.waitForFunction(() => {
      const s = window.uavReplay?.snapshot();
      return s?.mode === 'nominal' && !s.loading && s.run_id !== 'team-synthetic-browser-flight';
    });
    await page.locator('#flightV21').click();
    await checkTeamReplay(page);
    await page.getByTestId('team-result-choice').click();
    await page.getByRole('option', {name:'기존 대시보드 비행'}).click();
    await archive.waitFor();
    await page.waitForFunction(() => {
      const s = window.uavReplay?.snapshot();
      return s?.region === 'bundang' && !s.loading && s.run_id !== 'team-synthetic-browser-flight';
    });
    const cards = page.getByTestId('library-cards');
    await cards.locator('button[data-region=sangam_dmc]').click();
    await page.waitForFunction(() => window.uavReplay?.snapshot().region === 'sangam_dmc');
    await page.waitForFunction(() => document.querySelector('[data-testid=team-run-eligibility]')?.textContent.includes('검증용 배제 지역'));
    assert.equal(await start.isDisabled(), true);
    await cards.locator('button[data-region=bundang]').click();
    await checkTeamReplay(page);

    const other = await context.newPage();
    await other.goto(page.url());
    await other.waitForFunction(() => document.querySelector('[data-testid=team-run-eligibility]')?.textContent.includes('aaaaaaa'));
    const external = await (await page.request.post(new URL('/qa/external-checkout',page.url()).href)).json();
    assert.equal(external.starts, 1);
    await start.click();
    await page.waitForFunction(() => document.querySelector('[data-testid=team-run-eligibility]')?.textContent.includes('테스트 지도 자료 필요'));
    await other.waitForFunction(() => document.querySelector('[data-testid=team-source-version]')?.textContent.includes('bbbbbbb'));
    assert.equal(await start.isDisabled(),true);
    const repaired = await (await page.request.post(new URL('/qa/repair-assets',page.url()).href)).json();
    assert.equal(repaired.starts, 1); // stale displayed SHA never launched a job
    await page.getByTestId('team-source-sync').click();
    await page.waitForFunction(() => document.querySelector('[data-testid=team-run-eligibility]')?.textContent.includes('bbbbbbb'));
    assert.equal(await start.isEnabled(),true);
    await other.getByTestId('team-source-sync').click();
    await page.waitForFunction(() => document.querySelector('[data-testid=team-run-eligibility]')?.textContent.includes('갱신 중'));
    assert.equal(await start.isDisabled(),true);
    await page.waitForFunction(() => !document.querySelector('[data-testid=team-run-start]')?.disabled);
    await other.close();
    assert.match(await page.getByTestId('team-result-source').innerText(), /aaaaaaa.*bbbbbbb/);
    await page.getByTestId('team-run-scope').click();
    await page.getByRole('option', {name:'전체 70개 지역'}).click();
    assert.equal(await start.innerText(), '전체 70개 지역 새 비행 실행');
    assert.match(await page.getByTestId('team-run-eligibility').innerText(), /실행 가능 69개/);
    await start.click();
    await page.getByTestId('team-run-retry').waitFor();
    assert.match(await page.getByTestId('team-run-status').innerText(), /실패 69 · 실행 제외 1/);
    await page.getByTestId('team-run-retry').click();
    await page.waitForFunction(() => document.querySelector('[data-testid=team-run-status]')?.textContent.includes('대기 69'));
    assert.equal(await start.isDisabled(), true);
    await page.addInitScript(() => {
      window.openedFlights = [];
      function sample() {
        const surface = document.querySelector('#uavReplaySurface');
        const flight = window.uavReplay?.snapshot();
        if (surface?.dataset.loadState === 'ready' && !surface.closest('[inert]') &&
            flight?.run_id && !flight.loading && !window.openedFlights.includes(flight.run_id)) {
          window.openedFlights.push(flight.run_id);
        }
        if (!window.openedFlights.includes('team-synthetic-browser-flight')) requestAnimationFrame(sample);
      }
      requestAnimationFrame(sample);
    });
    await page.reload();
    await checkTeamReplay(page);
    await page.waitForFunction(() => window.openedFlights?.includes('team-synthetic-browser-flight'));
    assert.deepEqual(await page.evaluate(() => window.openedFlights), ['team-synthetic-browser-flight']);
    assert.equal(await start.isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('PASS: native team replay surface, recorded KPIs/clock/incident, archive switching, 70-region queue and failed retry (synthetic).');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
