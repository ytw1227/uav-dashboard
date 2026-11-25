// Read-only regression: no simulation/update controls. URL targets.json OUTPUT
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright-core');
const [url,targetsFile,output]=process.argv.slice(2);
const targets=JSON.parse(fs.readFileSync(targetsFile,'utf8').replace(/^\uFEFF/,''));
const pair=[targets.find(t=>t.region==='gwangju_20261104_b2_ba3bb4f31d'),
            targets.find(t=>t.region==='sejong_20261104_cc4d4ca34a')];
assert.ok(pair.every(Boolean),'Two previously failing consecutive selections must be included');

(async()=>{
    fs.mkdirSync(output,{recursive:true});
    const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
    const report={cases:[],errors:[]};
    try {
        const page=await browser.newPage({viewport:{width:1600,height:1100}});
        page.setDefaultTimeout(30000);
        page.on('pageerror',e=>report.errors.push(e.message));
        const card=t=>page.getByTestId('library-cards').locator('button[data-region="'+t.region+'"]');
        const ready=(t,mode='v21')=>page.waitForFunction(({t,mode})=>{
            const s=window.uavReplay?.snapshot();
            return s&&!s.loading&&s.region===t.region&&s.mode===mode&&
                s.run_id===(mode==='v21'?t.run_id:t.run_id.replace(/^team-/,'nominal-'));
        },{t,mode});
        const ack=(mode='v21')=>page.waitForFunction(mode=>{
            const s=window.uavReplay?.snapshot();
            const c=document.querySelector('[data-testid="library-cards"] button[aria-pressed=true]');
            return s&&!s.loading&&s.mode===mode&&c?.dataset.region===s.region&&
                c.dataset.flightMode===mode&&document.querySelector('[data-testid="library-mode"]')?.innerText
                    .includes(mode==='v21'?'예측·재배치 적용':'기본 · 미적용');
        },mode);
        await page.goto(url);
        await ack();
        for(let i=0;i<4;i++) {
            const from=pair[i%2],to=pair[(i+1)%2];
            await card(from).click(); await ready(from); await ack();
            await page.locator('#flightNominal').click();
            await ready(from,'nominal');
            await page.locator('#flightV21').click();
            await ready(from);
            // Deliberately do not wait for the server's mode acknowledgement.
            const before=await page.evaluate(()=>({replay:window.uavReplay.snapshot(),
                filter:document.querySelector('[data-testid="library-mode"]').innerText}));
            await card(to).click(); await ready(to); await ack();
            report.cases.push({kind:'rapid-mode-then-region',from:from.region,to:to.region,before,status:'passed'});
        }
        await page.getByTestId('library-mode').click();
        await page.getByRole('option',{name:'기본 · 미적용',exact:true}).click();
        // Exercise the opposite race too: a fresh filter intent must beat the
        // replay's previous mode, even before its new flight has been loaded.
        await card(pair[1]).click(); await ready(pair[1],'nominal'); await ack('nominal');
        report.cases.push({kind:'filter-mode-then-region',status:'passed'});
        await page.locator('#flightV21').click(); await ready(pair[1]); await ack();
        await page.getByTestId('library-sort').click();
        await page.getByRole('option',{name:'PDR 낮은 순',exact:true}).click();
        await card(pair[0]).click(); await ready(pair[0]); await ack();
        report.cases.push({kind:'sort-then-region',status:'passed'});
        await page.locator('#timeInput').fill('20'); await page.locator('#timeInput').press('Enter');
        await page.waitForFunction(()=>window.uavReplay.snapshot().time===20);
        const ticket=await page.evaluate(()=>window.uavReplay.snapshot().ticket);
        await card(pair[0]).focus(); await card(pair[0]).press('Enter');
        await page.waitForFunction(ticket=>{const s=window.uavReplay.snapshot();return s.ticket>ticket&&!s.loading&&s.time===0;},ticket);
        await ready(pair[0]);
        assert.equal(await page.evaluate(()=>window.uavReplay.snapshot().ticket),ticket+1,'Keyboard click opens exactly once');
        report.cases.push({kind:'same-card-keyboard-restart-once',status:'passed'});
        assert.deepEqual(report.errors,[]);
        console.log('PASS rapid region selection '+report.cases.length+' cases');
    } catch(error) {
        report.failure=String(error.stack||error);
        throw error;
    } finally {
        fs.writeFileSync(path.join(output,'rapid-region-selection.json'),JSON.stringify(report,null,2));
        await browser.close();
    }
})().catch(e=>{console.error(e);process.exitCode=1;});
