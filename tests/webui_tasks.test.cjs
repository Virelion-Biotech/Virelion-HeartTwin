const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.join(__dirname,'..');
const ui=path.join(root,'hearttwin','webui');
const tasks=require(path.join(ui,'tasks.js'));
const catalog=JSON.parse(fs.readFileSync(path.join(ui,'catalog.json'),'utf8')).services;
const html=fs.readFileSync(path.join(ui,'index.html'),'utf8');
const app=fs.readFileSync(path.join(ui,'app.js'),'utf8');

test('every registered service has a guided task and a safe non-health default operation',()=>{
  assert.equal(Object.keys(tasks.tasks).length,catalog.length);
  for(const service of catalog){
    const t=tasks.forService(service);
    assert.ok(t.title&&t.input&&t.output&&t.description,service.name);
    assert.ok(service.capabilities.includes(tasks.defaultCapability(service)),service.name);
    assert.doesNotMatch(tasks.defaultCapability(service),/\.(health|backends|ecosystem|tools)$/);
  }
});

test('task discovery supports measurement, recording, anatomy and research questions',()=>{
  const find=(query,category)=>catalog.filter(s=>tasks.matches(s,query,category)).map(s=>s.name);
  assert.ok(find('ECG','Recordings').includes('ElectroTrace'));
  assert.deepEqual(find('contraction','Recordings'),['MyoTrace']);
  assert.ok(find('geometry','Models').includes('CardiAnatomy'));
  assert.ok(find('patient-specific','Models').includes('PatientNative'));
  assert.deepEqual(find('ElectroTrace','Images'),[]);
});

test('both role paths offer the same three model contexts',()=>{
  assert.match(html,/data-workspace-role="researcher"/);
  assert.match(html,/data-workspace-role="doctor"/);
  for(const context of ['personalized','typical-male','typical-female']){
    assert.match(html,new RegExp('data-model-context="'+context+'"'));
  }
  assert.match(app,/state\.workspaceRole/);
  assert.match(app,/state\.modelContext/);
  assert.match(app,/state\.modelContext==='personalized'\?'patients':'overview'/);
});

test('reference-phenotype choices are explicit context, not a false claim of qualified anatomy',()=>{
  assert.match(html,/does not load sex-specific geometry/);
  assert.match(html,/A verified female anatomical\/physiological model is not currently bundled/);
  assert.match(app,/3D viewer still shows the bundled simplified adult-male surface, not a female model/i);
  assert.match(html,/not a qualified simulation-ready physiological model/);
  assert.match(app,/Doctor-facing research tools only: outputs are not validated for diagnosis or treatment decisions/);
});

test('gated molecular task stays gated and task labels preserve registered API identifiers',()=>{
  assert.equal(tasks.tasks.CardiLearn.advanced,true);
  assert.match(tasks.tasks.CardiLearn.description,/gated/);
  assert.equal(tasks.capabilityLabel('electrical.analyze'),'Analyze ECG or electrical recordings');
  assert.equal(tasks.defaultCapability(catalog.find(s=>s.name==='CardiEP')),'ep.simulate');
});
