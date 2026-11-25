const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright-core');
const [url,expectedFile,screens]=process.argv.slice(2);
const x=JSON.parse(fs.readFileSync(expectedFile,'utf8'));
async function main(){
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1050}});page.setDefaultTimeout(90000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);
  const region=page.getByTestId('library-cards').locator('button[data-region="bundang"]');
  await region.filter({hasText:x.sha.slice(0,7)}).waitFor();await region.click();
  await page.waitForFunction(r=>window.uavReplay?.snapshot()?.run_id===r&&!window.uavReplay.snapshot().loading,x.run);
  await page.waitForFunction(()=>window.__UAV_OPERATIONS_TEST__?.snapshot()?.status==='available');
  const panel=page.locator('#operationsPanel');
  assert.equal(await panel.locator('[data-drone]').count(),8);
  assert.match(await panel.locator('[data-drone="uav-4"]').innerText(),/R2.*고도 조정 중계/s);
  assert.match(await panel.locator('[data-drone="uav-6"]').innerText(),/R4.*고정 중계/s);
  const metrics=await page.locator('#metricCards .metric-value').allInnerTexts();
  assert.deepEqual(metrics,['99.13%','99.69%','3회','12.5 s']);
  await panel.locator('[data-drone="uav-4"]').click();
  async function seek(t){
   await page.locator('#timeInput').fill(String(t));await page.locator('#timeInput').press('Enter');
   await page.waitForFunction(t=>window.uavReplay.snapshot().time===t&&window.__UAV_OPERATIONS_TEST__.snapshot().readout?.time===t,t);
  }
  for(const t of [56,64,71.5,72,79,87,94,314.5,1370,1699.5,1729.5]){
   await seek(t);
   const view=await page.evaluate(()=>window.__UAV_OPERATIONS_TEST__.snapshot().readout);
   const row=x.operations.progress.find(r=>r.time_s===t);
   assert.equal(view.selected.height,row.heights_m[4],`height ${t}`);
   assert.equal(view.recordedTime,t);
   assert.equal(view.row.state,row.state);
   assert.equal(view.progress.reached,row.waypoints_reached);
   assert.match(await page.locator('#nodeDetail').innerText(),/고도 조정 중계/);
   if(t===79)assert.match(await panel.locator('[data-ops="stage"]').innerText(),/대기/);
   console.log(t,await panel.locator('[data-ops="stage"]').innerText(),view.selected.height);
  }
  await seek(71.5);
  assert.match(await page.locator('#fmFleetProbability').innerText(),/96\.8/);
  await panel.locator('[data-ops="chart-details"] summary').click();
  await page.waitForFunction(()=>document.querySelector('[data-ops="plot"]')?._fullLayout);
  const plotted=await page.locator('[data-ops="plot"]').evaluate(n=>n.data.map(t=>({name:t.name,x:t.x.length,y:t.y.length})));
  assert.equal(plotted[0].x,x.operations.progress.length);assert.equal(plotted.length,3);
  const drag=panel.locator('[data-ops="plot"] .nsewdrag').first();
  await drag.scrollIntoViewIfNeeded();const box=await drag.boundingBox();
  await page.mouse.move(box.x+box.width*.12,box.y+box.height*.3);await page.mouse.down();
  await page.mouse.move(box.x+box.width*.45,box.y+box.height*.7,{steps:12});await page.mouse.up();
  await page.waitForFunction(()=>{
   const a=document.querySelector('[data-ops="plot"]')._fullLayout.xaxis.range;return a[1]-a[0]<1000;
  });
  assert.equal(await page.evaluate(()=>window.uavReplay.snapshot().time),71.5,'drag must not seek');
  const zoom=await panel.locator('[data-ops="plot"]').evaluate(n=>n._fullLayout.xaxis.range.slice());
  await page.locator('#playButton').click();await page.waitForFunction(()=>window.uavReplay.snapshot().time>72);
  await page.locator('#playButton').click();
  assert.deepEqual(await panel.locator('[data-ops="plot"]').evaluate(n=>n._fullLayout.xaxis.range.slice()),zoom,'playback must preserve graph zoom');
  await panel.locator('[data-ops-action="full"]').click();
  await panel.locator('[data-ops="records-details"] summary').click();
  const step=panel.locator('[data-record="altitude-window-0-verified"]');
  await step.click();
  await page.waitForFunction(()=>Math.abs(window.uavReplay.snapshot().time-71.6)<1e-6);
  await seek(314.5);
  if(screens){fs.mkdirSync(screens,{recursive:true});await panel.screenshot({path:path.join(screens,'bundang-operations.png')});}
  await page.locator('#fmRadioTab').click();
  await page.locator('#fmNodeStates [data-inspect="uav-4"] [data-operations-role]').waitFor();
  assert.match(await page.locator('#fmNodeStates [data-inspect="uav-4"]').innerText(),/고도 조정 중계/);
  assert.match(await page.locator('#fmNodeStates [data-inspect="uav-6"]').innerText(),/고정 중계/);
  assert.equal(await page.locator('#fmNodeStates [data-inspect="uav-4"]').getAttribute('data-role'),'relay');
  if(screens)await page.locator('#fmNodeStates').screenshot({path:path.join(screens,'bundang-role-cards.png')});
  await page.locator('#incidentQuickSelect').selectOption(x.cut[0]);await page.locator('#incidentOpen').click();
  await page.locator('#incidentDialog[open][data-ready="true"]').waitFor();
  await page.locator('#opsIncidentReadout').waitFor({state:'visible'});
  assert.match(await page.locator('#opsIncidentReadout').innerText(),/고도/);
  await page.keyboard.press('Escape');
  await page.locator('#flightNominal').click();
  await page.waitForFunction(r=>window.uavReplay.snapshot().run_id===r&&!window.uavReplay.snapshot().loading,x.nominal);
  assert.equal(await panel.isVisible(),false);assert.equal(await page.locator('[data-operations-role]').count(),0);
  await page.locator('#flightV21').click();
  await page.waitForFunction(()=>window.__UAV_OPERATIONS_TEST__?.snapshot()?.status==='available');
  const other=page.getByTestId('library-cards').locator('button[data-region="sangam_dmc"]');await other.click();
  await page.waitForFunction(()=>window.uavReplay.snapshot().region==='sangam_dmc'&&!window.uavReplay.snapshot().loading);
  assert.equal(await panel.isVisible(),false);assert.equal(await page.locator('[data-operations-role]').count(),0);
  await region.click();await page.waitForFunction(()=>window.__UAV_OPERATIONS_TEST__.snapshot().status==='available');
  assert.deepEqual(await page.locator('#metricCards .metric-value').allInnerTexts(),metrics);
  assert.deepEqual(errors,[]);
  console.log('PASS: roles, measured altitude, native stages, graph zoom during playback, record navigation, incident readout and region/mode cleanup.');
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1});
