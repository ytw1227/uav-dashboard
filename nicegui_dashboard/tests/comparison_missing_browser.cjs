const assert = require('node:assert/strict');
const {chromium} = require('playwright-core');

async function main() {
    const browser = await chromium.launch({
        ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {channel: 'chrome'}),
        headless: true,
    });
    try {
        const page = await browser.newPage();
        page.setDefaultTimeout(20000);
        await page.goto(process.argv[2]);
        const checks = page.getByRole('checkbox', {name: '비교', exact: true});
        await checks.first().click();
        await page.getByText(/독립 실행 2건 · 비교 선택 1건/).waitFor();
        await checks.nth(1).click();
        await page.getByText(/독립 실행 2건 · 비교 선택 2건/).waitFor();
        await page.getByRole('tab', {name: '다중 비교'}).click();
        const metric = page.locator('.q-field').filter({hasText: '비교 지표'});
        await metric.locator('.q-select__dropdown-icon').click();
        await page.locator('.q-menu .q-item').filter({hasText: '(pdr)'}).click();
        await page.getByText('선택 2건 · 유효 1건 · 결측 1건', {exact: true}).waitFor();
        const summary = page.getByText(/표본 1 · 균등 평균 100.00%/);
        assert.equal(await summary.count(), 1);
        await page.getByText('synthetic_run_0002 · 미산출', {exact: true}).click();
        await page.getByText('최종 지표값이 기록되지 않았습니다.', {exact: true}).waitFor();
        await page.getByRole('button', {name: '비교 선택 모두 해제'}).click();
        await page.getByText('보관 비행에서 비교할 실행을 선택하세요.', {exact: true}).waitFor();
        assert.equal(await summary.count(), 0);
        console.log('PASS: same-condition selection, valid/missing counts, mean, per-run reason, clear');
    } finally {
        await browser.close();
    }
}

main().catch(error => {console.error(error); process.exitCode = 1;});
