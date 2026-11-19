import {createServer} from 'node:net';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const slot=createServer();slot.listen(0,'127.0.0.1');await once(slot,'listening');const port=slot.address().port;await new Promise(resolve=>slot.close(resolve));
const base=`http://127.0.0.1:${port}`,server=spawn(process.execPath,[fileURLToPath(new URL('../server.mjs',import.meta.url)),'--port',String(port)],{stdio:['ignore','pipe','pipe']});let stderr='';server.stderr.on('data',x=>stderr+=x);
const post=async(action,body,headers={})=>{const r=await fetch(base+'/api/exchange/'+action,{method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(body)});const text=await r.text();return [r.status,r.headers.get('content-type')?.includes('application/json')?JSON.parse(text):{error:text}];};
try{
 await Promise.race([new Promise((resolve,reject)=>{server.stdout.once('data',resolve);server.once('exit',()=>reject(new Error(stderr)));}),new Promise((_,reject)=>{const t=setTimeout(()=>reject(new Error('Startup timeout')),5000);t.unref();})]);
 const content=await readFile(new URL('../assets/exchange-example.pge.json',import.meta.url),'utf8');
 let [status,data]=await post('import',{format:'package_json',content});assert.equal(status,200,JSON.stringify(data));const packet=data.result.package;assert.equal(data.result.report.task_preserved,true);
 [status,data]=await post('export',{format:'studio_csv',package:packet});assert.equal(status,200,JSON.stringify(data));const csv=data.result.content;
 [status,data]=await post('import',{format:'studio_csv',content:csv});assert.equal(status,200);assert.deepEqual(data.result.package,packet);
 [status,data]=await post('evaluate',{package:packet});assert.equal(status,200,JSON.stringify(data));assert.equal(data.result.passed,true);
 [status,data]=await post('import',{format:'package_json',content,path:'/tmp/unsafe'});assert.equal(status,400);
 [status,data]=await post('import',{format:'package_json',content},{Origin:'http://localhost:5173'});assert.equal(status,403);
 const matpower=await readFile(new URL('../assets/exchange-example.m',import.meta.url),'utf8');
 [status,data]=await post('import',{format:'matpower_v2',content:matpower});assert.equal(status,200,JSON.stringify(data));assert.equal(data.result.report.requires_acknowledgement,true);assert.ok(data.result.package.conversion);
 [status,data]=await post('import',{format:'matpower_v2',content:matpower+"system('whoami');"});assert.equal(status,422);
 [status,data]=await post('export',{format:'project_json',package:packet});assert.equal(status,422);
 console.log('Exchange HTTP passed:project/task CSV roundtrip,real task PF,origin isolation,strict fields,MATPOWER reports and executable-code rejection.');
}finally{if(server.exitCode===null&&server.signalCode===null){const closed=once(server,'exit');server.kill('SIGTERM');await closed;}}
