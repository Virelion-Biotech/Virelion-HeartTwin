const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ui=path.join(__dirname,'../hearttwin/webui');
const tasks=require(path.join(ui,'tasks.js'));
const services=JSON.parse(fs.readFileSync(path.join(ui,'catalog.json'))).services;
test('every research task resolves to a registered operation, never a health check',()=>{
  assert.equal(Object.keys(tasks.tasks).length,services.length);
  for(const service of services){
    const t=tasks.forService(service);
    assert.ok(t.input&&t.output&&t.description,service.name);
    assert.ok(service.capabilities.includes(tasks.defaultCapability(service)),service.name);
    assert.doesNotMatch(tasks.defaultCapability(service),/\.(health|backends|ecosystem)$/);
  }
});
test('clinician and researcher search finds tasks by data and intended measurement',()=>{
  const find=(q,c)=>services.filter(s=>tasks.matches(s,q,c)).map(s=>s.name);
  assert.ok(find('ECG','Recordings').includes('ElectroTrace'));
  assert.deepEqual(find('contraction','Recordings'),['MyoTrace']);
  assert.ok(find('pressure','Models').includes('CardiFlow'));
  assert.deepEqual(find('ElectroTrace','Images'),[]);
});
test('primary recording tasks and unavailable molecular task stay distinct',()=>{
  for(const name of ['ElectroTrace','MyoTrace','OptiCell','CardioScore'])assert.equal(tasks.tasks[name].advanced,false);
  assert.equal(tasks.tasks.CardiLearn.advanced,true);
  assert.match(tasks.tasks.CardiLearn.description,/unavailable/);
  assert.match(tasks.tasks.CardiSimNative.description,/surface reference/);
});
test('readable capability labels do not change the registered operation identifiers',()=>{
  assert.equal(tasks.capabilityLabel('electrical.analyze'),'Analyze ECG or electrical recordings');
  assert.equal(tasks.capabilityLabel('ep.calibrate'),'Electrical modeling · Calibrate');
  assert.notEqual(tasks.capabilityLabel('ep.health'),tasks.capabilityLabel('flow.health'));
});
