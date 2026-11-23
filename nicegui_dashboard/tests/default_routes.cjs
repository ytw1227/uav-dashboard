// Run against a local dashboard with both fixed HTML files and a result service.
const assert = require('node:assert/strict');
const {chromium} = require('playwright-core');

const origin = new URL(process.argv[2] || 'http://127.0.0.1:8092').origin;
const expectedRun = 'run-bf52b084d14211e0386491f5';
const expectedRevision = 'rev-93c4b5d31ad8c5d913e8216d';
const expectedEvent = 'event-000032';

async function checkLibrary(page) {
  await page.waitForFunction(() => {
    const state = window.uavReplay?.snapshot();
    return state?.source === 'B' && !state.loading &&
      document.querySelectorAll('[data-testid=library-cards] button[data-region]').length === 70;
  });
  const cards = page.getByTestId('library-cards').locator('button[data-region]');
  const regions = await cards.evaluateAll(nodes => nodes.map(node => node.dataset.region));
  assert.equal(new Set(regions).size, 70);
  assert.ok(regions.includes('sangam_dmc'));
  assert.equal(await cards.first().isVisible(), true);
  assert.equal(await page.locator('header a[aria-current=page]').getAttribute('href'), '/regions');
}

async function checkSangam(page, source) {
  await page.waitForFunction(source => {
    const state = window.uavReplay?.snapshot();
    const map = window.__UAV_MAP_TEST__?.snapshot();
    return state?.source === source && state.region === 'sangam_dmc' && state.mode === 'v21' &&
      !state.loading && map?.ready && map.region === 'sangam_dmc' &&
      document.querySelector('#incidentQuickSelect option[value="event-000032"]');
  }, source);
  const flight = await page.evaluate(() => window.uavReplay.snapshot());
  assert.deepEqual([flight.run_id, flight.revision], [expectedRun, expectedRevision]);
  const selected = page.locator('#incidentQuickSelect option:checked');
  assert.equal(await selected.getAttribute('value'), expectedEvent);
  assert.match((await selected.textContent()).replaceAll(',', ''), /사건 13\s*·\s*2845\.5초/);
  await page.locator('#incidentOpen').click();
  const dialog = page.locator('#incidentDialog[open][data-ready=true]');
  await dialog.waitFor();
  const detail = await dialog.evaluate(node => {
    const {event, start, end, renderedCursor} = node.dataset;
    return [event, Number(start), Number(end), Number(renderedCursor)];
  });
  assert.deepEqual(detail, [expectedEvent, 2845.5, 2889, 2845.5]);
}

async function smoke() {
  const executablePath = process.env.CHROME_PATH;
  const browser = await chromium.launch({
    ...(executablePath ? {executablePath} : {channel: 'chrome'}),
    headless: true,
    args: ['--enable-unsafe-swiftshader'],
  });
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.setDefaultTimeout(90_000);
    await page.goto(origin);
    assert.equal(new URL(page.url()).pathname, '/regions');
    await checkLibrary(page);

    await page.locator('header a[href="/local"]').click();
    await page.getByTestId('local-registry-details').waitFor();
    const connected = page.getByText(/서비스 연결됨 · 등록 자료 \d+건 · 세대 \d+/);
    await connected.waitFor();
    const registry = await connected.innerText();
    assert.equal(await page.locator('header a[aria-current=page]').getAttribute('href'), '/local');
    const archive = await page.getByText(/지도 \d+개 · 독립 실행 \d+건 · 비교 선택 \d+건/).innerText();
    const archivedFlights = Number(archive.match(/독립 실행 (\d+)건/)[1]);
    if (archivedFlights > 0) {
      await page.getByTestId('local-open-pin').waitFor();
      await page.getByText(/현재까지 공개된 최신 관측 \d+건/).waitFor();
      await page.locator('canvas[aria-label^="보관 비행 지도 ·"]').waitFor();
    } else {
      await page.getByText('현재 조건에서 재생할 수 있는 보관 비행이 없습니다.').waitFor();
    }
    await page.getByRole('tab', {name: '다중 비교'}).click();
    await page.getByText('보관 비행에서 비교할 실행을 선택하세요.', {exact: true}).waitFor();

    await page.locator('header a[href="/regions"]').click();
    await checkLibrary(page);
    await page.locator('[data-testid=library-cards] button[data-region=sangam_dmc]').click();
    await checkSangam(page, 'B');
    await page.goto(new URL('/sangam', origin).href);
    await checkSangam(page, 'S');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({regions: 70, registry, archivedFlights, sangamSources: ['B', 'S'],
      event: expectedEvent, start: 2845.5, end: 2889, pageErrors: errors}));
  } finally {
    await browser.close();
  }
}

smoke().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
