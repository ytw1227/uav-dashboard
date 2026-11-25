// Read-only regression on the prepared Bundang sample; never starts a flight.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright-core');
const [url, expectedFile, screenshots] = process.argv.slice(2);
const expected = JSON.parse(fs.readFileSync(expectedFile, 'utf8'));
const controls = ['starts_per_h','cut_seconds_per_h','timely_pdr','relay_m'];
const forecasts = ['recall','precision','false_per_h','average_precision'];
const ratios = new Set(['timely_pdr','recall','precision','average_precision']);
const knownControl = {
    starts_per_h:6.2445793581960105,
    cut_seconds_per_h:26.019080659150045,
    timely_pdr:0.985814782692493,
    relay_m:20371.635636485382,
};

function numeric(text) {
    return Number(text.replace(/,/g,'').match(/[-+]?\d+(?:\.\d+)?/)?.[0] ?? NaN);
}

async function main() {
    const browser = await chromium.launch({channel:'chrome', headless:true,
        args:['--enable-unsafe-swiftshader']});
    try {
        const page = await browser.newPage({viewport:{width:1600,height:1050}});
        page.setDefaultTimeout(90000);
        const errors=[];
        page.on('pageerror',error=>errors.push(error.message));
        await page.goto(url);
        const card=page.getByTestId('library-cards').locator('button[data-region="bundang"]');
        await card.filter({hasText:expected.sha.slice(0,7)}).waitFor();
        await card.click();
        await page.waitForFunction(run=>window.uavReplay?.snapshot().run_id===run &&
            !window.uavReplay.snapshot().loading &&
            window.__UAV_OPERATIONS_TEST__?.snapshot().status==='available',expected.run);
        assert.deepEqual(await page.locator('#metricCards .metric-value').allInnerTexts(),
            ['99.13%','99.69%','3회','12.5 s']);
        assert.equal(await page.evaluate(()=>Boolean(document.getElementById('flightMetricsPanel')
            .compareDocumentPosition(document.getElementById('operationsPanel')) & Node.DOCUMENT_POSITION_FOLLOWING)),true,
            'Existing communication and forecast panel precedes flight operations');

        async function seek(time) {
            await page.locator('#timeInput').fill(String(time));
            await page.locator('#timeInput').press('Enter');
            await page.waitForFunction(time=>window.uavReplay.snapshot().time===time &&
                window.__UAV_MAP_TEST__?.snapshot().renderedTime===time &&
                window.__UAV_MAP_TEST__.snapshot().ready,time);
        }
        await seek(71.5);
        assert.match(await page.locator('#fmFleetProbability').innerText(),/96\.8%/);
        await seek(314.5);
        await page.locator('#fmRadioTab').click();
        await page.locator('#fmNodeStates [data-inspect="uav-4"][data-operations-duty="altitude_relay"]').waitFor();
        await page.locator('#fmNodeStates [data-inspect="uav-6"][data-operations-duty="fixed_relay"]').waitFor();
        const painted=await page.evaluate(async()=>{
            const source=window.uavReplay.snapshot().source;
            const {aircraftPaint}=await import('/part2/replay/'+source+'/map_status.js');
            const get=(id,status)=>aircraftPaint({nodes:[{id,role:'relay',status,text:status}]},id).color;
            const trace=window.__UAV_MAP_TEST__.plot().data.find(t=>t.meta?.role==='drones');
            return {
                connected:{ordinary:get('uav-3','connected'),altitude:get('uav-4','connected'),fixed:get('uav-6','connected')},
                risk:[get('uav-4','risk'),get('uav-6','risk')],
                down:[get('uav-4','down'),get('uav-6','down')],
                unknown:[get('uav-4','unknown'),get('uav-6','unknown')],
                plotted:Object.fromEntries(trace.customdata.map((id,i)=>[id,trace.marker.color[i]])),
            };
        });
        assert.deepEqual(painted.connected,{ordinary:'#a3afff',altitude:'#4d79ed',fixed:'#4d79ed'});
        assert.deepEqual(painted.risk,['#ffd44f','#ffd44f']);
        assert.deepEqual(painted.down,['#ff6677','#ff6677']);
        assert.deepEqual(painted.unknown,['#9baaba','#9baaba']);
        assert.equal(painted.plotted['uav-4'],painted.connected.altitude);
        assert.equal(painted.plotted['uav-6'],painted.connected.fixed);

        const response=await page.request.get(new URL('/part2/team/'+expected.run+'/'+expected.revision+'/model',url).href);
        assert.ok(response.ok());
        const model=await response.json();
        assert.equal(model.run_id,expected.run);
        assert.equal(model.revision,expected.revision);
        const regional=model.profile?.regional;
        assert.ok(regional,'Pinned regional model evaluation must be available');
        assert.deepEqual(regional.missing || [],[]);
        for(const [key,value] of Object.entries(knownControl))
            assert.ok(Math.abs(regional.control[key]-value)<1e-9,key+' preserves the official applied-flight aggregation');
        assert.equal(regional.forecast.events,1);
        assert.equal(regional.forecast.hits,1);
        assert.equal(regional.forecast.alerts,3);
        assert.equal(regional.forecast.recall,1);
        assert.equal(regional.forecast.precision,1/3);
        assert.ok(Number.isFinite(regional.forecast.average_precision));

        async function checkModel(nominal=false) {
            await page.locator('button[data-uav-tab="performance"]').click();
            const section=page.locator('#performanceContent [data-regional-model="bundang"]');
            await section.waitFor();
            assert.equal(await section.locator('[data-metric]').count(),8);
            for(const key of [...controls,...forecasts]) {
                const raw=(controls.includes(key)?regional.control:regional.forecast)[key];
                assert.ok(Number.isFinite(raw),key+' is calculated');
                const text=await section.locator('[data-metric="'+key+'"] strong').innerText();
                assert.ok(Math.abs(numeric(text)-raw*(ratios.has(key)?100:1))<=.0051,key+': '+text+' / '+raw);
            }
            const text=await page.locator('#performanceContent').innerText();
            assert.equal(await page.locator('#performanceContent [data-model-component]').count(),3);
            assert.equal(await page.locator('#performanceContent [data-model-blend="75-17.5-7.5"]').count(),1);
            assert.match(await page.locator('#performanceContent .model-eyebrow').innerText(),/분당/);
            assert.match(text,/별도.*평가|평가.*기준 비행/);
            assert.match(text,/전체 지역.*구분/);
            if(nominal)assert.match(text,/기본 대형으로 비행 중.*짝지어 비교하는 모델 적용 비행/s);
            return text;
        }
        await checkModel();
        if(screenshots) {
            fs.mkdirSync(screenshots,{recursive:true});
            await page.locator('#performanceContent').screenshot({path:path.join(screenshots,'bundang-model-eight.png')});
        }
        await page.locator('button[data-uav-tab="map"]').click();
        await page.locator('#flightNominal').click();
        await page.waitForFunction(run=>window.uavReplay.snapshot().run_id===run &&
            !window.uavReplay.snapshot().loading,expected.nominal);
        assert.equal(await page.locator('#operationsPanel').isVisible(),false);
        assert.equal(await page.locator('[data-operations-role]').count(),0);
        assert.equal(await page.locator('[data-operations-legend]').count(),0);
        assert.deepEqual(await page.locator('#metricCards .metric-value').allInnerTexts(),
            ['96.3%','98.65%','16회','62 s']);
        await checkModel(true);
        await page.locator('button[data-uav-tab="map"]').click();
        await page.locator('#flightV21').click();
        await page.waitForFunction(()=>window.__UAV_OPERATIONS_TEST__.snapshot().status==='available');
        const other=page.getByTestId('library-cards').locator('button[data-region="sangam_dmc"]');
        await other.click();
        await page.waitForFunction(()=>window.uavReplay.snapshot().region==='sangam_dmc'&&!window.uavReplay.snapshot().loading);
        await page.locator('button[data-uav-tab="performance"]').click();
        await page.waitForFunction(()=>document.querySelector('#performanceContent')?.innerText &&
            !document.querySelector('#performanceContent').innerText.includes('불러옵니다'));
        await page.waitForFunction(()=>!document.querySelector('[data-regional-model="bundang"]'));
        assert.equal(await page.locator('[data-regional-model="bundang"]').count(),0);
        await card.click();
        await page.waitForFunction(run=>window.uavReplay.snapshot().run_id===run&&!window.uavReplay.snapshot().loading,expected.run);
        await page.getByTestId('team-result-choice').click();
        await page.getByRole('option',{name:'기존 대시보드 비행'}).click();
        await page.waitForFunction(()=>!window.uavReplay.snapshot().team_context&&!window.uavReplay.snapshot().loading);
        await page.locator('button[data-uav-tab="performance"]').click();
        await page.waitForFunction(()=>!document.querySelector('[data-regional-model="bundang"]'));
        assert.equal(await page.locator('[data-regional-model="bundang"]').count(),0);
        assert.equal(await page.locator('[data-operations-legend]').count(),0);
        assert.deepEqual(errors,[]);
        console.log('PASS: eight measured model metrics, same applied model on nominal, preserved KPIs/forecast, panel order, special role colors with unchanged warnings, and region/archive cleanup.',
            JSON.stringify({regional,painted}));
    } finally {await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
