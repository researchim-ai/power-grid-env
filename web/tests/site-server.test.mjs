import {spawn,spawnSync} from 'node:child_process';
import {cp,mkdtemp,readFile,stat,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
// Build after copying just web/, without a simulator checkout or Python.
const detached=await mkdtemp(path.join(tmpdir(),'power-grid-site-'));
try{
 const source=fileURLToPath(new URL('..',import.meta.url));
 await cp(source,detached,{recursive:true,filter:file=>!['dist','node_modules'].includes(path.basename(file))});
 const built=spawnSync(process.execPath,['scripts/build.mjs'],{cwd:detached,env:{...process.env,PGE_STUDIO_URL:'https://studio.example.test/app'},encoding:'utf8'});
 assert.equal(built.status,0,built.stderr);
 const config=JSON.parse(await readFile(path.join(detached,'dist/site.config.json'),'utf8'));
 assert.equal(config.studio_url,'https://studio.example.test/app/');
 for(const file of ['editor.html','server.mjs','assets/grid-replays.json'])await assert.rejects(stat(path.join(detached,'dist',file)));
 assert.ok((await readFile(path.join(detached,'dist/blog.html'),'utf8')).includes('site-links.js'));
}finally{await rm(detached,{recursive:true,force:true});}
const port=55195,base=`http://127.0.0.1:${port}`;
const server=spawn(process.execPath,[fileURLToPath(new URL('../server.mjs',import.meta.url)),'--port',String(port)],{env:{...process.env,PGE_PYTHON:'/nonexistent/python',PGE_STUDIO_URL:'https://studio.example.test/app'},stdio:['ignore','pipe','pipe']});
let stderr='';server.stderr.on('data',value=>stderr+=value);
try{
 await Promise.race([new Promise((resolve,reject)=>{server.stdout.once('data',resolve);server.once('exit',()=>reject(new Error(stderr)));}),new Promise((_,reject)=>{const timer=setTimeout(()=>reject(new Error('Website startup timeout')),5000);timer.unref();})]);
 for(const path of ['/','/blog.html','/blog-training.html','/blog-physics.html','/blog-workbench.html','/site-links.js','/assets/learning-experiment.json'])assert.equal((await fetch(base+path)).status,200,path);
 for(const path of ['/editor.html','/grid.html','/steady.html','/agent-lab.html','/assets/grid-replays.json','/api/health','/server.mjs','/tests/site-server.test.mjs','/../studio/editor.html'])assert.equal((await fetch(base+path)).status,404,path);
 for(const path of ['/api/editor/solve','/api/agent/tasks','/api/agent/reset'])assert.equal((await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).status,404,path);
 assert.equal((await fetch(base+'/index.html',{method:'POST'})).status,405);
 const config=await (await fetch(base+'/site.config.json')).json();assert.equal(config.studio_url,'https://studio.example.test/app/');assert.equal(config.PGE_PYTHON,undefined);
 const page=await (await fetch(base+'/blog-training.html')).text();assert.ok(page.includes('data-studio-page'));assert.ok(!page.includes('href="./editor.html"'));
 assert.equal((await fetch(base+'/styles.css',{method:'HEAD'})).status,200);
 console.log('Website isolation passed: standalone pages/config, no Python requirement, no engineering API/pages/recordings or server sources.');
}finally{if(server.exitCode===null&&server.signalCode===null){const closed=once(server,'exit');server.kill('SIGTERM');await closed;}}
