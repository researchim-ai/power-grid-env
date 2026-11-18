import {spawn} from 'node:child_process';import {once} from 'node:events';import {fileURLToPath} from 'node:url';import assert from 'node:assert/strict';
const port=55194,base=`http://127.0.0.1:${port}`,server=spawn(process.execPath,[fileURLToPath(new URL('../server.mjs',import.meta.url)),'--port',String(port)],{stdio:['ignore','pipe','pipe']});let stderr='';server.stderr.on('data',v=>stderr+=v);
const post=async(action,body,headers={})=>{const r=await fetch(base+'/api/agent/'+action,{method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(body)});return [r.status,await r.json()];};
try{
 await Promise.race([new Promise((resolve,reject)=>{server.stdout.once('data',resolve);server.once('exit',()=>reject(new Error(stderr)));}),new Promise((_,reject)=>{const timer=setTimeout(()=>reject(new Error('Startup timeout')),5000);timer.unref();})]);
 let [status,data]=await post('tasks',{});assert.equal(status,200);assert.equal(data.tasks.length,7);assert.ok(data.tasks.every(t=>t.split==='train'&&/^task-[a-f0-9]{24}$/.test(t.id)));assert.ok(data.tasks.every(t=>!Object.hasOwn(t,'bug')&&!Object.hasOwn(t,'parents')));
 const build=data.tasks.find(t=>t.kind==='assemble'),diagnosis=data.tasks.find(t=>t.kind==='diagnose');
 [status,data]=await post('reset',{task_id:build.id,state:{reward:1}});assert.equal(status,400);
 [status,data]=await post('reset',{task_id:'star-assemble'});assert.equal(status,422);
 [status,data]=await post('reset',{task_id:build.id});assert.equal(status,200);const sid=data.session_id;assert.ok(sid);assert.equal(data.private_state,undefined);assert.equal(data.observation.task.id,build.id);
 const call=async(name,args)=>{const [st,d]=await post('tool',{session_id:sid,name,arguments:args});assert.equal(st,200,JSON.stringify(d));assert.equal(d.private_state,undefined);return d.response;};
 [status,data]=await post('tool',{session_id:sid,name:'simulate',arguments:{},state:{last_result:{converged:true}}});assert.equal(status,400);
 let r=await call('unknown',{});assert.equal(r.ok,false);assert.equal(r.observation.calls_remaining,31);
 for(let i=0;i<build.requirements.routes.length;i++){const route=build.requirements.routes[i];r=await call('add_line',{route_id:route.id,equipment_id:'userline'+i,catalog_id:'copper-1'});assert.equal(r.ok,true);}
 for(const load of build.requirements.loads){r=await call('add_load',{load_id:load.id});assert.equal(r.ok,true);}
 r=await call('simulate',{});assert.ok(r.ok&&r.result.converged);assert.equal(r.observation.has_fresh_measurements,true);
 [status,data]=await post('observe',{session_id:sid});assert.equal(status,200);assert.equal(data.private_state,undefined);assert.equal(data.observation.has_fresh_measurements,true);assert.equal(data.observation.calls_remaining,r.observation.calls_remaining);assert.ok(data.measurements.converged);
 r=await call('submit',{});assert.equal(r.result.reward,1);assert.equal(r.observation.finished,true);
 [status,data]=await post('tool',{session_id:sid,name:'submit',arguments:{}});assert.equal(status,422);
 r=await call('export',{});assert.equal(r.eligible_for_gold_sft,false);assert.ok(r.messages.some(m=>m.role==='tool'));assert.equal(JSON.stringify(r).includes('private_state'),false);
 [status,data]=await post('close',{session_id:sid});assert.equal(status,200);[status,data]=await post('close',{session_id:sid});assert.equal(status,200);
 [status,data]=await post('tool',{session_id:sid,name:'simulate',arguments:{}});assert.equal(status,404);
 [status,data]=await post('reset',{task_id:diagnosis.id});const dsid=data.session_id;assert.equal(status,200);assert.ok(!data.observation.task.tools.includes('set_line_catalog'));
 [status,data]=await post('tool',{session_id:dsid,name:'set_line_catalog',arguments:{equipment_id:'line1',catalog_id:'copper-1'}});assert.equal(status,200);assert.equal(data.response.ok,false);
 [status,data]=await post('close',{session_id:dsid});assert.equal(status,200);
 console.log('Agent API integration passed:train-only inventory,opaque IDs,private-state isolation,owner-field rejection,real construction/PF/reward,read-only diagnosis,terminal/export/close.');
}finally{if(server.exitCode===null&&server.signalCode===null){const exited=once(server,'exit');server.kill('SIGTERM');await exited;}}
