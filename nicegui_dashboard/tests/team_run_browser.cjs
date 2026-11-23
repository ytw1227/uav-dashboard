// Invoked by the temporary local service in team_run_browser.py.
const assert = require('node:assert/strict');
const {chromium} = require('playwright-core');

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
    const current = page.getByTestId('team-recorded-flight');
    await current.locator('canvas[aria-label^="보관 비행 지도"]').waitFor();
    assert.match(await page.getByTestId('team-result-source').innerText(), /aaaaaaa.*0.5초/);
    assert.equal(new URL(page.url()).pathname, '/regions');
    const archive = page.locator('#uavReplaySurface');
    assert.equal(await archive.isHidden(), true);
    await page.getByTestId('team-result-choice').click();
    await page.getByRole('option', {name:'기존 대시보드 비행'}).click();
    await archive.waitFor();
    assert.equal(await current.isHidden(), true);
    const cards = page.getByTestId('library-cards');
    await cards.locator('button[data-region=sangam_dmc]').click();
    await page.waitForFunction(() => window.uavReplay?.snapshot().region === 'sangam_dmc');
    await page.waitForFunction(() => document.querySelector('[data-testid=team-run-eligibility]')?.textContent.includes('검증용 배제 지역'));
    assert.equal(await start.isDisabled(), true);
    await cards.locator('button[data-region=bundang]').click();
    await current.locator('canvas[aria-label^="보관 비행 지도"]').waitFor();

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
    await page.reload();
    await current.locator('canvas[aria-label^="보관 비행 지도"]').waitFor();
    assert.equal(await start.isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('PASS: single run button, recorded bundle/map on card, archived replay, 70-region queue and failed retry (synthetic).');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
