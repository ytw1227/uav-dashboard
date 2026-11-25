// Read-only browser verification on a measured flight, using the checks in native_flight_browser.py.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright-core');
const [url, expectedFile] = process.argv.slice(2);
const x = JSON.parse(fs.readFileSync(expectedFile, 'utf8'));
const keys = ['search_connected_mean','pdr','persistent_onsets','persistent_cut_seconds'];
const quantity = text => Number(text.replace(/,/g,'').match(/[-+]?\d+(?:\.\d+)?/)?.[0] ?? NaN);

function checkValue(text, expected, tolerance=.0051) {
    const value = quantity(text);
    assert.ok(Number.isFinite(value) && Math.abs(value-expected) <= tolerance,
              `화면 표시 ${text} / 기록 ${expected}`);
}

async function checkArm(page,index,nominalAvailable=Boolean(x.nominal)) {
    const rid = index === 0 ? x.run : x.nominal;
    await page.waitForFunction(([rid,rev]) => {
        const snapshot = window.uavReplay?.snapshot(), map = window.__UAV_MAP_TEST__?.snapshot();
        return snapshot?.run_id === rid && snapshot.revision === rev && !snapshot.loading &&
               map?.ready && map.renderedRunId === rid;
    },[rid,x.revision]);
    const cards = await page.locator('#metricCards .metric-value').allInnerTexts();
    assert.equal(cards.length,4);
    const m=x.metrics[index];
    for (const [i,v] of [m.ctr*100,m.pdr*100,m.persistent_onsets,m.persistent_cut_seconds].entries())
        checkValue(cards[i],v);
    assert.equal(await page.locator('#flightNominal').isEnabled(),nominalAvailable);
    return cards;
}

async function main() {
    const browser = await chromium.launch({
        ...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{channel:'chrome'}),
        headless:true,args:['--enable-unsafe-swiftshader'],
    });
    try {
        const page=await browser.newPage({viewport:{width:1500,height:900}});
        page.setDefaultTimeout(120_000);
        const errors=[];
        page.on('pageerror',e=>errors.push(e.message));
        await page.goto(url);
        const region=page.getByTestId('library-cards').locator(`button[data-region="${x.region}"]`);
        await region.filter({hasText:x.sha.slice(0,7)}).waitFor();
        await region.click();
        const appliedCards=await checkArm(page,0,Boolean(x.nominal) && !x.lateNominal);
        console.log('Applied cards/3D',JSON.stringify(appliedCards));
        const clock=page.locator('#timeInput');
        if (x.lateNominal) {
            await clock.fill('5');
            await clock.press('Enter');
            await page.waitForFunction(()=>window.uavReplay.snapshot().time===5);
            assert.match(await page.locator('#flightTabNote').innerText(), /재생 경로/);
            const response=await page.request.post(new URL('/qa/register-nominal',url).href);
            assert.ok(response.ok());
            await page.waitForFunction(()=>!document.getElementById('flightNominal').disabled);
            const current=await page.evaluate(()=>window.uavReplay.snapshot());
            assert.deepEqual([current.run_id,current.revision,current.time],[x.run,x.revision,5]);
            assert.equal(await page.locator('#flightTabNote').innerText(),'');
            console.log('Registered nominal while the applied flight stayed at 5 seconds');
        }
        if (x.forecast) {
            await clock.fill(String(x.forecast[0]));
            await clock.press('Enter');
            await page.waitForFunction(t=>window.uavReplay.snapshot().time===t,x.forecast[0]);
            const forecast=page.locator('#fmFleetProbability');
            await forecast.waitFor();
            checkValue(await forecast.innerText(),x.forecast[1]*100,.051);
            console.log('Forecast scrub',x.forecast[0],await forecast.innerText());
        }

        if (x.cut) {
            await page.locator('#incidentQuickSelect').selectOption(x.cut[0]);
            await page.locator('#incidentOpen').click();
            const dialog=page.locator('#incidentDialog[open][data-ready="true"]');
            await dialog.waitFor();
            assert.deepEqual(await dialog.evaluate(n=>[n.dataset.event,+n.dataset.start,+n.dataset.end]),x.cut);
            const pattern=dialog.locator('.pattern-name');
            await pattern.waitFor({state:'attached'});
            console.log('Incident',x.cut,await pattern.innerText(),await dialog.locator('.factor-name').innerText());
            await page.keyboard.press('Escape');
            await dialog.waitFor({state:'hidden'});
        } else console.log('No newly started persistent outage in the recorded flight');

        await page.locator('#fmEvaluationTab').click();
        if (x.evaluation) {
            await page.waitForFunction(run=>window.__FLIGHT_METRICS_TEST__?.evaluationSnapshot()?.activeRunId===run,x.run);
            const evaluation=await page.evaluate(()=>window.__FLIGHT_METRICS_TEST__.evaluationSnapshot());
            assert.equal(evaluation.validationError,'');
            assert.deepEqual(evaluation.summary,x.evaluation);
            assert.equal(evaluation.referenceController,'warm_05s');
            console.log('Independent regional evaluation',evaluation.summary.hits,evaluation.summary.events,await page.locator('#feRecall').innerText());
        } else {
            await page.locator('#fmEvaluationView').filter({hasText:/별도 평가한 기록이 없어/}).waitFor();
            console.log('Historical run has no measured regional evaluation; archived value hidden');
        }

        await page.locator('button[data-uav-tab="compare"]').click();
        const table=page.locator('#pairedTable');
        await table.locator('tbody tr').first().waitFor();
        assert.equal(await table.locator('tbody tr').count(),9);
        for (const [i,mode] of ['v21','nominal'].entries()) for (const [j,key] of keys.entries()) {
            const field=j===0?'ctr':key;
            const expected=x.metrics[i][field]*(j<2?100:1);
            checkValue(await table.locator(`tr[data-metric="${key}"] td[data-arm="${mode}"]`).innerText(),expected);
        }
        await page.locator('button[data-uav-tab="performance"]').click();
        const model=page.locator('#performanceContent');
        await model.getByText('현재 비행에 사용한 모델',{exact:true}).waitFor();
        const text=await model.innerText();
        assert.ok(text.includes(x.controller));
        assert.match(text,/0\.5초/);
        assert.match(text,/선행 탐지 성능/);
        assert.match(text,/제어 성능/);
        console.log('Comparison and model',x.controller,x.revision);
        await page.locator('button[data-uav-tab="map"]').click();
        if (x.nominal) {
            await page.locator('#flightNominal').click();
            const nominalCards=await checkArm(page,1);
            await region.filter({hasText:'기본 대형'}).waitFor();
            console.log('Native nominal cards/3D',JSON.stringify(nominalCards));
            await page.locator('#flightV21').click();
            await checkArm(page,0);
            await region.filter({hasText:'팀 실행'}).waitFor();
        } else {
            assert.equal(await page.locator('#flightNominal').isDisabled(),true);
            console.log('Historical native nominal trajectory unavailable, switch disabled');
        }
        await page.getByTestId('team-result-choice').click();
        await page.getByRole('option',{name:'기존 대시보드 비행'}).click();
        await page.waitForFunction(region=>{
            const s=window.uavReplay?.snapshot(),m=window.__UAV_MAP_TEST__?.snapshot();
            return s?.region===region && s.run_id && !s.team_context && !s.loading && m?.renderedRunId===s.run_id && m.ready;
        },x.region);
        const archiveRun=await page.evaluate(()=>window.uavReplay.snapshot().run_id);
        await page.locator('#fmEvaluationTab').click();
        await page.waitForFunction(r=>window.__FLIGHT_METRICS_TEST__?.evaluationSnapshot()?.activeRunId===r,archiveRun);
        const archived=await page.evaluate(()=>window.__FLIGHT_METRICS_TEST__.evaluationSnapshot());
        assert.equal(archived.validationError,'');
        console.log('Archive 3D and restored archived evaluation',archiveRun,archived.summary.hits,archived.summary.events);
        assert.deepEqual(errors,[]);
        console.log('PASS: regional 3D, cards, comparison, model and archive switching.', {
            nominalReplay:Boolean(x.nominal), regionalEvaluation:Boolean(x.evaluation),
            recordedForecast:Boolean(x.forecast), incident:Boolean(x.cut),
        });
    } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1});
