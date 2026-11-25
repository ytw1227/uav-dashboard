// Read-only audit of already published flights; never clicks update or execution controls.
// node all_regions_enrichment_browser.cjs URL targets.json independent_pin_audit.json OUTPUT [LIMIT]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright-core');
const [url, targetsFile, auditFile, output, limitText] = process.argv.slice(2);
const targets = JSON.parse(fs.readFileSync(targetsFile, 'utf8').replace(/^\uFEFF/, ''));
const before = JSON.parse(fs.readFileSync(auditFile, 'utf8').replace(/^\uFEFF/, ''));
const controlKeys = ['starts_per_h', 'cut_seconds_per_h', 'timely_pdr', 'relay_m'];
const forecastKeys = ['recall', 'precision', 'false_per_h', 'average_precision'];
const percentages = new Set(['timely_pdr', 'recall', 'precision', 'average_precision']);
const specialColor = '#4d79ed';
const number = text => Number(text.replace(/,/g, '').match(/[-+]?\d+(?:\.\d+)?/)?.[0] ?? NaN);
const baseFor = target => '/part2/team/' + target.run_id + '/' + target.revision;

function checkRounded(text, value, label, tolerance=.0051) {
    if (Number.isFinite(value)) assert.ok(Math.abs(number(text)-value) <= tolerance, label+': '+text+' != '+value);
    else assert.match(text, /—|미산출|산출 불가/, label+' must not invent a missing value');
}

async function main() {
    fs.mkdirSync(output, {recursive:true});
    const report = {api:[], ui:[], failures:[], pageErrors:[]};
    const save = () => fs.writeFileSync(path.join(output, 'all-regions-browser.json'), JSON.stringify(report,null,2));
    const browser = await chromium.launch({channel:'chrome', headless:true, args:['--enable-unsafe-swiftshader']});
    try {
        const context = await browser.newContext({viewport:{width:1600,height:1100}});
        const page = await context.newPage();
        page.setDefaultTimeout(90000);
        page.on('pageerror', error => report.pageErrors.push(error.message));
        async function waitLibraryMode(mode, region) {
            // The replay finishes in the browser before its debounced state reaches
            // NiceGUI. Wait for the server-owned filter and replacement card DOM;
            // otherwise the next click can target a button being removed by that ack.
            await page.waitForFunction(({mode,region}) => {
                const snapshot=window.uavReplay?.snapshot();
                const selected=document.querySelector('[data-testid="library-cards"] button[aria-pressed="true"]');
                const filter=document.querySelector('[data-testid="library-mode"]');
                return snapshot && !snapshot.loading && snapshot.mode===mode &&
                    (!region || snapshot.region===region) && selected &&
                    selected.dataset.region===snapshot.region &&
                    filter?.innerText.includes(mode==='v21'?'예측·재배치 적용':'기본 · 미적용') &&
                    selected.innerText.includes(mode==='v21'?'적용 모델':'기본 대형');
            }, {mode,region});
            await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        }
        async function read(endpoint) {
            const response = await context.request.get(new URL(endpoint,url).href, {timeout:90000});
            assert.equal(response.status(),200,endpoint+' HTTP status');
            return response.json();
        }
        const measured = new Map();
        // Every target receives API/source checks, even when LIMIT restricts costly visual checks.
        for (const target of targets) {
            try {
                const base=baseFor(target), descriptor=await read(base+'/descriptor');
                const model=await read(base+'/model'), operations=await read(base+'/operations');
                const comparison=await read(base+'/comparison');
                const manifest=descriptor.manifest, regional=model.profile?.regional;
                assert.equal(manifest.run_id,target.run_id);
                assert.equal(manifest.bundle_revision,target.revision);
                assert.equal(manifest.team_commit,target.team_commit);
                assert.equal(manifest.region,target.region);
                const previous=before.details.find(row=>row.region===target.region);
                assert.ok(previous,'Independent pre-update snapshot exists');
                for (const [key,value] of Object.entries(previous.metrics))
                    assert.equal(manifest.final_metrics[key],value,target.region+' retained KPI '+key);
                assert.deepEqual(descriptor.nominal_replay,previous.pin.nominal);
                assert.equal(comparison.status,'ready');
                assert.equal(comparison.applied.run_id,target.run_id);
                assert.equal(comparison.applied.bundle_revision,target.revision);
                assert.equal(comparison.nominal.run_id,descriptor.nominal_replay.run_id);
                assert.equal(comparison.nominal.bundle_revision,target.revision);
                for (const [key,value] of Object.entries(previous.metrics))
                    assert.equal(comparison.applied.final_metrics[key],value,'Comparison retains applied '+key);
                assert.equal(model.run_id,target.run_id);
                assert.equal(model.revision,target.revision);
                assert.ok(regional,target.region+' regional model');
                assert.equal(regional.scope.region,target.region);
                assert.equal(regional.scope.official_release,false);
                for (const key of controlKeys) assert.ok(Number.isFinite(regional.control[key]),key+' official control result');
                for (const key of forecastKeys) {
                    const value=regional.forecast[key];
                    if (value != null) assert.ok(Number.isFinite(value),key+' finite or explicitly absent');
                    if (key!=='average_precision') assert.ok(Number.isFinite(value),key+' official forecast result');
                }
                const f=regional.forecast;
                assert.ok(Number.isInteger(f.ranking_rows) && Number.isInteger(f.ranking_positives),'AP evaluation scope recorded');
                if (f.ranking_rows>0) assert.ok(Number.isFinite(f.average_precision),'AP exists for eligible observations');
                if (f.ranking_rows>0 && f.ranking_positives===0) assert.ok(f.average_precision===0,'Official AP for no positive frames (including -0)');
                if (f.ranking_rows===0) assert.ok(f.average_precision==null,'No eligible observations is not AP zero');
                assert.equal(operations.status,'available');
                assert.equal(operations.identity.run_id,target.run_id);
                assert.equal(operations.identity.revision,target.revision);
                assert.equal(operations.identity.team_commit,target.team_commit);
                assert.deepEqual(operations.roles.map(role=>role.drone_id),manifest.fleet.map(node=>node.id));
                assert.equal(operations.progress.length,Math.round(manifest.simulation_end_s*2)+1);
                const result={target,descriptor,model,operations,comparison};
                measured.set(target.region,result);
                report.api.push({region:target.region,run:target.run_id,ranking_rows:f.ranking_rows,
                    ranking_positives:f.ranking_positives,ap:f.average_precision,
                    special_roles:operations.roles.filter(role=>['fixed_relay','altitude_relay'].includes(role.duty))});
            } catch(error) { report.failures.push({phase:'api',region:target.region,error:String(error.stack||error)}); }
            save();
        }
        await page.goto(url);
        await page.getByTestId('library-cards').locator('button[data-region]').first().waitFor();
        await waitLibraryMode('v21');
        const uiTargets=limitText ? targets.slice(0,Number(limitText)) : targets;
        for (const target of uiTargets) {
            if (!measured.has(target.region)) continue;
            try {
                const {descriptor,model,operations,comparison}=measured.get(target.region);
                const regional=model.profile.regional;
                const card=page.getByTestId('library-cards').locator('button[data-region="'+target.region+'"]');
                const label=(await card.innerText()).split('\n')[0].trim();
                await card.click();
                await page.waitForFunction(pin=>window.uavReplay?.snapshot().run_id===pin.run &&
                    !window.uavReplay.snapshot().loading && window.__UAV_OPERATIONS_TEST__?.snapshot().status==='available',
                    {run:target.run_id});
                assert.equal(await page.evaluate(()=>window.uavReplay.snapshot().region),target.region);
                const metricValues=await page.locator('#metricCards .metric-value').allInnerTexts();
                // The rendered cards retain their original four definitions.
                const m=descriptor.manifest.final_metrics;
                checkRounded(metricValues[0],m.ctr*100,'CTR');
                checkRounded(metricValues[1],m.pdr*100,'PDR');
                checkRounded(metricValues[2],m.persistent_outage_count ?? m.persistent_onsets,'New persistent outages');
                checkRounded(metricValues[3],m.persistent_cut_seconds,'Persistent outage time',.051);
                assert.equal(await page.evaluate(()=>Boolean(document.getElementById('flightMetricsPanel')
                    .compareDocumentPosition(document.getElementById('operationsPanel')) & Node.DOCUMENT_POSITION_FOLLOWING)),true);
                const time=Math.min(20,Math.max(0,descriptor.manifest.simulation_end_s-10));
                await page.locator('#timeInput').fill(String(time));
                await page.locator('#timeInput').press('Enter');
                await page.waitForFunction(t=>window.uavReplay.snapshot().time===t &&
                    window.__UAV_MAP_TEST__?.snapshot().ready && window.__UAV_MAP_TEST__.snapshot().renderedTime===t,time);
                await page.locator('#fmRadioTab').click();
                for (const role of operations.roles) {
                    const tile=page.locator('#fmNodeStates [data-inspect="'+role.drone_id+'"]');
                    if (['fixed_relay','altitude_relay'].includes(role.duty))
                        assert.equal(await tile.getAttribute('data-operations-duty'),role.duty);
                    else assert.ok(!await tile.getAttribute('data-operations-duty'),role.drone_id+' must not inherit another region special duty');
                }
                const paints=await page.evaluate(async roles=>{
                    const source=window.uavReplay.snapshot().source;
                    const {aircraftPaint}=await import('/part2/replay/'+source+'/map_status.js');
                    return roles.filter(role=>role.role==='relay').map(role=>({id:role.drone_id,duty:role.duty,
                        colors:Object.fromEntries(['connected','risk','down','unknown'].map(status=>[status,
                            aircraftPaint({nodes:[{id:role.drone_id,role:'relay',status,text:status}]},role.drone_id).color]))}));
                },operations.roles);
                for (const paint of paints) {
                    assert.equal(paint.colors.connected,['fixed_relay','altitude_relay'].includes(paint.duty)?specialColor:'#a3afff');
                    assert.equal(paint.colors.risk,'#ffd44f');
                    assert.equal(paint.colors.down,'#ff6677');
                    assert.equal(paint.colors.unknown,'#9baaba');
                }
                await page.locator('button[data-uav-tab="performance"]').click();
                const regionPanel=page.locator('#performanceContent [data-regional-model="'+target.region+'"]');
                await regionPanel.waitFor();
                assert.equal(await page.locator('#performanceContent [data-regional-model]').count(),1);
                assert.equal(await regionPanel.locator('[data-metric]').count(),8);
                for (const key of [...controlKeys,...forecastKeys]) {
                    const raw=(controlKeys.includes(key)?regional.control:regional.forecast)[key];
                    checkRounded(await regionPanel.locator('[data-metric="'+key+'"] strong').innerText(),
                        Number.isFinite(raw)?raw*(percentages.has(key)?100:1):raw,key);
                }
                assert.ok((await page.locator('.model-eyebrow').innerText()).includes(label),'Actual displayed region name');
                assert.equal(await page.locator('[data-model-component]').count(),3);
                await page.locator('[data-model-blend="75-17.5-7.5"]').waitFor();
                for (const duty of ['fixed_relay','altitude_relay']) {
                    const adopted=operations.roles.some(role=>role.duty===duty);
                    assert.equal(await page.locator('[data-model-duty="'+duty+'"]').getAttribute('data-adopted'),String(adopted));
                }
                if (regional.forecast.ranking_rows>0 && regional.forecast.ranking_positives===0)
                    assert.match(await page.locator('#performanceContent').innerText(),/AP 평가 시점이 0개/);
                if (regional.forecast.ranking_rows===0)
                    assert.match(await page.locator('#performanceContent').innerText(),/유효한 관측 시점이 없어/);
                await page.locator('button[data-uav-tab="compare"]').click();
                const table=page.locator('#pairedTable');
                await table.locator('tbody tr').first().waitFor();
                assert.equal(await table.locator('tbody tr').count(),9);
                for (const [arm,metrics] of [['v21',comparison.applied.final_metrics],['nominal',comparison.nominal.final_metrics]]) {
                    for (const key of ['search_connected_mean','pdr','persistent_onsets','persistent_cut_seconds']) {
                        const raw=metrics[key==='search_connected_mean'?'ctr':key];
                        checkRounded(await table.locator('tr[data-metric="'+key+'"] td[data-arm="'+arm+'"]').innerText(),
                            raw*(['search_connected_mean','pdr'].includes(key)?100:1),'Comparison '+arm+' '+key);
                    }
                }
                await page.locator('button[data-uav-tab="map"]').click();
                await page.locator('#flightNominal').click();
                await page.waitForFunction(run=>window.uavReplay.snapshot().run_id===run&&!window.uavReplay.snapshot().loading,
                    descriptor.nominal_replay.run_id);
                await waitLibraryMode('nominal',target.region);
                assert.equal(await page.locator('#operationsPanel').isVisible(),false);
                assert.equal(await page.locator('[data-operations-legend]').count(),0);
                const nominalCards=await page.locator('#metricCards .metric-value').allInnerTexts();
                const nominal=comparison.nominal.final_metrics;
                for (const [index,value] of [nominal.ctr*100,nominal.pdr*100,nominal.persistent_onsets,nominal.persistent_cut_seconds].entries())
                    checkRounded(nominalCards[index],value,'Nominal card '+index);
                await page.locator('button[data-uav-tab="performance"]').click();
                await regionPanel.waitFor();
                for (const key of [...controlKeys,...forecastKeys]) {
                    const raw=(controlKeys.includes(key)?regional.control:regional.forecast)[key];
                    checkRounded(await regionPanel.locator('[data-metric="'+key+'"] strong').innerText(),
                        Number.isFinite(raw)?raw*(percentages.has(key)?100:1):raw,'Nominal paired model '+key);
                }
                assert.match(await page.locator('#performanceContent').innerText(),/기본 대형으로 비행 중.*짝지어 비교하는 모델 적용 비행/s);
                await page.locator('button[data-uav-tab="map"]').click();
                await page.locator('#flightV21').click();
                await page.waitForFunction(run=>window.uavReplay.snapshot().run_id===run&&!window.uavReplay.snapshot().loading,target.run_id);
                await waitLibraryMode('v21',target.region);
                report.ui.push({region:target.region,label,metrics:metricValues,roles:paints,status:'passed'});
            } catch(error) {
                const state=await page.evaluate(()=>({replay:window.uavReplay?.snapshot(),
                    operations:window.__UAV_OPERATIONS_TEST__?.snapshot(),
                    filter:document.querySelector('[data-testid="library-mode"]')?.innerText,
                    selected:document.querySelector('[data-testid="library-cards"] button[aria-pressed="true"]')?.dataset.region})).catch(()=>null);
                report.failures.push({phase:'ui',region:target.region,error:String(error.stack||error),state});
                await page.screenshot({path:path.join(output,'failure-'+target.region+'.png')}).catch(()=>{});
            }
            save();
        }
        assert.deepEqual(report.failures,[]);
        assert.deepEqual(report.pageErrors,[]);
        console.log('PASS all-region supplemental metrics and roles:',JSON.stringify({api:report.api.length,ui:report.ui.length}));
    } finally { save(); await browser.close(); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
