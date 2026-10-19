'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {parseGeometry,schematic} = require('../hearttwin/webui/viewer.js');
const geometry = {node_xyz:[[0,0,0],[1,0,0],[0,1,0],[0,0,1],[0,0,-1]],tetrahedra:[[0,1,2,3],[0,2,1,4]],units:{node_xyz:'mm'}};

test('two tetrahedra expose six boundary faces; their shared face is removed',()=>{
 const m=parseGeometry({geometry});
 assert.equal(m.faces.length,6);
 assert(!m.faces.some(f=>[...f].sort().join(',')==='0,1,2'));
 assert.equal(m.units,'mm');
 assert.equal(m.schematic,false);
});
test('triangular surfaces preserve indexing and normalize all coordinates with one scale',()=>{
 const m=parseGeometry({node_xyz:[[10,20,30],[12,20,30],[10,24,30]],triangles:[[0,1,2]]});
 assert.deepEqual(m.faces,[[0,1,2]]);
 assert.deepEqual(m.nodes,[[-1,-2,0],[1,-2,0],[-1,2,0]]);
 assert.equal(m.units,'unspecified');
});
test('completed job geometry pairs only with same-order finite activation values',()=>{
 const values=[0,1,2,3,4];
 const m=parseGeometry({request:{payload:{geometry}},result:{activation_ms:values}});
 assert.deepEqual(m.activation,values);
 assert.throws(()=>parseGeometry({geometry,activation_ms:[0]}),/one finite value per node/);
 assert.throws(()=>parseGeometry({geometry,activation_ms:[0,1,null,3,4]}),/one finite value per node/);
 assert.throws(()=>parseGeometry({geometry,activation_ms:[0,1,NaN,3,4]}),/one finite value per node/);
 assert.throws(()=>parseGeometry({geometry,activation_ms:[-1e308,1e308,0,0,0]}),/numerical display limits/);
});
test('malformed cells, nonfinite coordinates and nonmanifold tetrahedra are rejected',()=>{
 assert.throws(()=>parseGeometry({...geometry,tetrahedra:[[0,1,2,99]]}),/valid four-node/);
 assert.throws(()=>parseGeometry({...geometry,tetrahedra:[[0,1,1,3]]}),/valid four-node/);
 assert.throws(()=>parseGeometry({...geometry,node_xyz:[[Infinity,0,0],[1,0,0],[0,1,0]]}),/finite numeric/);
 assert.throws(()=>parseGeometry({...geometry,tetrahedra:[[0,1,2,3],[0,2,1,4],[1,0,2,4]]}),/Non-manifold/);
 assert.throws(()=>parseGeometry({node_xyz:[[0,0,0],[0,0,0],[0,0,0]],triangles:[[0,1,2]]}),/nonzero/);
 assert.throws(()=>parseGeometry({node_xyz:[[0,0,0],[1,0,0],[0,1,0]],triangles:null}),/triangles/);
});
test('oversized meshes fail explicitly without silently reducing scientific data',()=>{
 assert.throws(()=>parseGeometry({node_xyz:Array.from({length:30001},()=>[0,0,0]),triangles:[[0,1,2]]}),/30,000/);
 assert.throws(()=>parseGeometry({node_xyz:geometry.node_xyz,triangles:Array.from({length:60001},()=>[0,1,2])}),/60,000/);
});
test('reference schematic has valid selectable layers and contains no simulated activation',()=>{
 const m=schematic();
 assert.equal(m.schematic,true);assert.equal(m.activation,null);assert.equal(m.layers.length,6);
 assert.equal(m.faceLayer.length,m.faces.length);
 assert(m.faces.every(f=>f.every(i=>Number.isInteger(i)&&i>=0&&i<m.nodes.length)));
 assert(m.nodes.every(p=>p.every(Number.isFinite)));
 assert.equal(new Set(m.faceLayer).size,6);
});
