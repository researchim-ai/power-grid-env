// Explicit local integration check: PGE_PYTHON=... npm run check:api
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
import assert from 'node:assert/strict';
import {initialDraft,transformerExample,steadyExample,acExample} from '../editor-model.js';
import {validateBundle} from '../grid-model.js';
const port=55193,base=`http://127.0.0.1:${port}`;
const server=spawn(process.execPath,[fileURLToPath(new URL('../server.mjs',import.meta.url)),'--port',String(port)],{stdio:['ignore','pipe','pipe']});
let errors='';server.stderr.on('data',b=>errors+=b);
try{
 await Promise.race([new Promise((resolve,reject)=>{server.stdout.once('data',resolve);server.once('exit',()=>reject(new Error(errors)));}),new Promise((_,reject)=>{const timer=setTimeout(()=>reject(new Error('Server startup deadline')),5000);timer.unref();})]);
 const health=await (await fetch(base+'/api/health')).json();assert.equal(health.application,'power-grid-studio');assert.equal(health.training_jobs,false);assert.equal(health.model_connections,false);assert.equal((await fetch(base+'/api/health',{headers:{Origin:'https://untrusted.invalid'}})).status,403);
 const post=(draft,headers={})=>fetch(base+'/api/editor/validate',{method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(draft)});
 assert.equal((await fetch(base+'/api/editor/validate')).status,405);
 assert.equal((await post(initialDraft(),{Origin:'https://untrusted.invalid'})).status,403);
 assert.equal((await post(initialDraft(),{'Content-Type':'text/plain'})).status,415);
 assert.equal((await post('x'.repeat(262145))).status,413);
 let response=await post({...initialDraft(),script:'unsupported'});assert.equal(response.status,422);assert.equal((await response.json()).ok,false);
 response=await post(initialDraft(),{Origin:base});assert.equal(response.status,200);assert.equal((await response.json()).result.valid,true);
 const draft=initialDraft();draft.duration_s=.2;draft.motors[1].start_s=.1;draft.motors[0].model='double_cage';draft.motors[0].thermal_enabled=true;draft.loads=[{id:'z1',node_id:'n1',p_kw:20,q_kvar:5}];draft.events=[{time_s:.05,kind:'source_voltage',equipment_id:'source',value:.8}];
 response=await fetch(base+'/api/editor/solve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(draft)});const payload=await response.json();assert.equal(response.status,200,payload.error);validateBundle(payload.result);
 const s=payload.result.designs[0].studies[0];assert.equal(s.time_s.at(-1),.2);assert.ok(s.sample_side.includes('post_event'));assert.ok(s.series.motors.m1.current_a.some(v=>v>0));assert.ok(s.series.loads.z1.p_kw[0]>0);assert.ok(s.series.motors.m1.thermal_level);
 const {observationBundle}=await import('../grid-model.js');const snapshot=observationBundle(payload.result,payload.result.designs[0],s,0);validateBundle(snapshot);assert.deepEqual(snapshot.designs[0].studies[0].events,[]);assert.equal(snapshot.designs[0].studies[0].series.motors.m1.thermal_level,undefined);
 response=await fetch(base+'/api/editor/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(transformerExample())});const native=await response.json();assert.equal(response.status,200,native.error);assert.equal(native.result.network.transformers.length,1);assert.equal(native.result.network.voltage_levels.length,2);assert.equal(native.result.induction.motors[0].rated_ll_v,400);
 const multi=transformerExample();multi.duration_s=.2;response=await fetch(base+'/api/editor/solve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(multi)});const solved=await response.json();assert.equal(response.status,200,solved.error);validateBundle(solved.result);const md=solved.result.designs[0],ms=md.studies[0];assert.ok(ms.series.branches.tr1.receiving_current_a[0]>ms.series.branches.tr1.current_a[0]);const exported=observationBundle(solved.result,md,ms,0);validateBundle(exported);assert.equal(exported.designs[0].graph.nodes.find(n=>n.node_id==='hv').nominal_ll_v,10000);assert.equal(exported.designs[0].graph.branches.find(b=>b.corridor_id==='tr1').vector_group,'Dyn11');
 const {validateSteady}=await import('../steady-model.js');const solveStatic=async d=>{const r=await fetch(base+'/api/editor/solve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});return [r,await r.json()];};
 let pd=steadyExample();let [pr,pf]=await solveStatic(pd);assert.equal(pr.status,200,pf.error);validateSteady(pf.result);assert.ok(pf.result.source.p_kw>0);
 pd.switches[0].closed=false;[pr,pf]=await solveStatic(pd);assert.equal(pr.status,200,pf.error);validateSteady(pf.result);assert.ok(pf.result.nodes.some(n=>!n.energized));assert.equal(pf.result.branches.find(b=>b.id==='q1').from_terminal,null);
 const fd=steadyExample(true);[pr,pf]=await solveStatic(fd);assert.equal(pr.status,200,pf.error);validateSteady(pf.result);assert.ok(pf.result.fault.current_a>0);
 response=await fetch(base+'/api/editor/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(fd)});const fnative=await response.json();assert.equal(response.status,200,fnative.error);assert.equal(fnative.result.network.study_faults.length,1);assert.equal(fnative.result.induction,null);assert.ok(fnative.result.network.lines.some(l=>l.id==='q1'));
 fd.switches[0].closed=false;[pr,pf]=await solveStatic(fd);assert.equal(pr.status,422);assert.match(pf.error,/deenergized/);
 pd=steadyExample();pd.motors=initialDraft().motors;[pr,pf]=await solveStatic(pd);assert.equal(pr.status,422);assert.match(pf.error,/motors/);
 const ad=acExample();[pr,pf]=await solveStatic(ad);assert.equal(pr.status,200,pf.error);validateSteady(pf.result);assert.ok(pf.result.source.p_kw<0);assert.ok(pf.result.generators[0].actual.p_kw>0);
 Object.assign(ad.generators[0],{mode:'pv',v_set_pu:1.05,q_min_kvar:-2,q_max_kvar:2});[pr,pf]=await solveStatic(ad);assert.equal(pr.status,200,pf.error);validateSteady(pf.result);assert.equal(pf.result.generators[0].q_limited,true);assert.equal(pf.result.generators[0].q_at_bound,true);assert.ok(pf.result.generators[0].voltage_error_pu<0);assert.ok(Math.abs(pf.result.loads[0].law_check.p_residual_kw)<1e-5);
 response=await fetch(base+'/api/editor/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(ad)});const anative=await response.json();assert.equal(response.status,200,anative.error);assert.equal(anative.result.network.generators[0].mode,'pv');assert.equal(anative.result.network.loads[1].model,'zip');
 ad.profile='passive_3ph_fault';[pr,pf]=await solveStatic(ad);assert.equal(pr.status,422);assert.match(pf.error,/require ac_power_flow/);
 console.log('Editor API:19 integration checks passed (origin,methods,limits,validation,actual physics/replay)');
}finally{server.kill('SIGTERM');await once(server,'exit');}
