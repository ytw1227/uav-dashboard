// Invoked by the isolated team_sync_browser.py fixture.
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright-core');

const [url, origin, checkout] = process.argv.slice(2);

function publish(text) {
  fs.writeFileSync(path.join(origin, 'team-code.txt'), text);
  execFileSync('git', ['add', 'team-code.txt'], {cwd: origin});
  execFileSync('git', ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
    '-c', 'commit.gpgsign=false', 'commit', '-qm', text], {cwd: origin});
  return execFileSync('git', ['rev-parse', 'HEAD'], {cwd: origin, encoding: 'utf8'}).trim();
}

async function main() {
  const first = publish('fixture one');
  const browser = await chromium.launch({
    ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {channel: 'chrome'}),
    headless: true, args: ['--enable-unsafe-swiftshader'],
  });
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    page.setDefaultTimeout(60_000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(new URL('/regions', url).href);
    const button = page.getByTestId('team-source-sync');
    const status = page.getByTestId('team-source-status');
    const version = page.getByTestId('team-source-version');
    await page.getByTestId('team-source-panel').waitFor();
    await page.locator('[data-testid=library-cards] button[data-region=sangam_dmc]').click();
    await page.waitForFunction(() => {
      const s = window.uavReplay?.snapshot();
      return s?.region === 'sangam_dmc' && !s.loading;
    });
    await page.locator('#timeInput').fill('10');
    await page.locator('#timeInput').press('Enter');
    await page.waitForFunction(() => window.uavReplay.snapshot().time === 10);

    function selectedFlight() {
      return page.evaluate(() => {
        const s = window.uavReplay.snapshot();
        return [s.region, s.run_id, s.revision, s.time, s.mode, s.tab, s.event_id];
      });
    }
    const original = await selectedFlight();
    async function sync(expectedState, expectedVersion, verifyBusy = true) {
      await button.click();
      if (verifyBusy) {
        await page.locator('[data-testid=team-source-status][data-state=busy]').waitFor();
        assert.equal(await button.isDisabled(), true);
      }
      await page.locator(`[data-testid=team-source-status][data-state=${expectedState}]`).waitFor();
      assert.equal(await button.isEnabled(), true);
      assert.ok((await version.innerText()).includes(expectedVersion.slice(0, 7)));
      const info = await (await page.request.get(new URL('/api/team-source', url).href)).json();
      assert.equal(info.commit, expectedVersion);
      assert.deepEqual(await selectedFlight(), original);
    }
    await sync('updated', first);
    const next = publish('fixture two');
    await sync('updated', next);
    await sync('current', next);
    fs.writeFileSync(path.join(checkout, 'team-code.txt'), 'local edit');
    await sync('error', next, false);
    assert.match(await status.innerText(), /로컬 변경/);
    assert.equal(fs.readFileSync(path.join(checkout, 'team-code.txt'), 'utf8'), 'local edit');
    assert.deepEqual(errors, []);
    console.log('PASS: browser clone, fast-forward, no-op, local-edit protection and playback selection.');
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
