import http from 'node:http';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {readFile,stat} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.dirname(fileURLToPath(import.meta.url));
const args=process.argv.slice(2);const portArg=args.indexOf('--port');
const port=Number(portArg>=0?args[portArg+1]:process.env.PORT||5174);
if(!Number.isInteger(port)||port<1||port>65535)throw new Error('Expected a port between 1 and 65535');
const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.svg':'image/svg+xml','.woff':'font/woff','.txt':'text/plain; charset=utf-8','.md':'text/plain; charset=utf-8'};
let active=null;const episodes=new Map();const episodeTTL=3600000;
async function api(req,res,url){
  const hosts=new Set([`localhost:${port}`,`127.0.0.1:${port}`]);
  const origin=req.headers.origin;
  if(!hosts.has(req.headers.host)||origin&&!['http://localhost:'+port,'http://127.0.0.1:'+port].includes(origin)){res.writeHead(403);return res.end('Local same-origin requests only');}
  const send=(status,data)=>{if(!res.destroyed&&!res.writableEnded&&!res.headersSent){res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(data));}};
  if(req.method!=='POST'){res.writeHead(405,{Allow:'POST'});return res.end();}
  if(!/^application\/json(?:;|$)/i.test(req.headers['content-type']||''))return send(415,{error:'JSON required'});
  if(active)return send(409,{error:'Другой расчёт ещё выполняется'});
  let size=0,chunks=[];
  for await(const chunk of req){size+=chunk.length;if(size>262144)return send(413,{error:'Предел проекта256KB'});chunks.push(chunk);}
  let draft;try{draft=JSON.parse(Buffer.concat(chunks));}catch{return send(400,{error:'Некорректный JSON'});}
  if(active)return send(409,{error:'Другой расчёт ещё выполняется'});
  let workerRequest,episodeId=null;const agent=url.pathname.startsWith('/api/agent/'),action=url.pathname.split('/').at(-1);
  if(agent){
    for(const [id,item]of episodes)if(Date.now()-item.updated>episodeTTL)episodes.delete(id);
    const keys={tasks:[],reset:['task_id'],tool:['session_id','name','arguments'],close:['session_id'],observe:['session_id']}[action];
    if(!keys||!draft||typeof draft!=='object'||Array.isArray(draft)||Object.keys(draft).sort().join()!==[...keys].sort().join())return send(400,{error:'Invalid agent protocol fields'});
    if(action==='tasks')workerRequest={action:'tasks'};
    else if(action==='reset'){if(typeof draft.task_id!=='string'||draft.task_id.length>100)return send(400,{error:'Invalid task ID'});if(episodes.size>=32)return send(429,{error:'Episode capacity32;close an episode first'});workerRequest={action:'reset',task_id:draft.task_id};episodeId=randomUUID();}
    else{
      if(typeof draft.session_id!=='string'||!/^[-a-f0-9]{36}$/.test(draft.session_id))return send(400,{error:'Invalid episode ID'});
      episodeId=draft.session_id;
      if(action==='close'){episodes.delete(episodeId);return send(200,{ok:true,closed:true});}
      if(!episodes.has(episodeId))return send(404,{error:'Unknown or expired episode'});
      if(action==='observe')workerRequest={action:'observe',state:episodes.get(episodeId).state};
      else{if(typeof draft.name!=='string'||draft.name.length>64||!draft.arguments||typeof draft.arguments!=='object'||Array.isArray(draft.arguments)||JSON.stringify(draft.arguments).length>8192)return send(400,{error:'Invalid tool/arguments;limit8KB'});
      workerRequest={action:'tool',state:episodes.get(episodeId).state,name:draft.name,arguments:draft.arguments};}
    }
  }else if(url.pathname.startsWith('/api/exchange/')){
    const expected={import:['format','content'],export:['format','package'],evaluate:['package']}[action];
    if(!expected||!draft||typeof draft!=='object'||Array.isArray(draft)||Object.keys(draft).sort().join()!==[...expected].sort().join())return send(400,{error:'Invalid exchange protocol fields'});
    workerRequest={...draft,action};
  }else workerRequest={action,draft};
  const child=spawn(process.env.PGE_PYTHON||'python3',['-m',agent?'benchmarks.engineering_agent':url.pathname.startsWith('/api/exchange/')?'benchmarks.studio_exchange':'benchmarks.grid_editor'],{cwd:path.dirname(root),env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',OPENBLAS_NUM_THREADS:'1',OMP_NUM_THREADS:'1'},stdio:['pipe','pipe','pipe']});
  active=child;let output=[],bytes=0,failed=false,stderr='';
  const timer=setTimeout(()=>{failed=true;child.kill('SIGKILL');},45000);
  res.on('close',()=>{if(!res.writableEnded)child.kill('SIGKILL');});
  child.stdout.on('data',chunk=>{bytes+=chunk.length;if(bytes>12000000){failed=true;child.kill('SIGKILL');}else output.push(chunk);});
  child.stderr.on('data',chunk=>{stderr=(stderr+chunk.toString()).slice(-2000);});
  child.on('error',()=>{clearTimeout(timer);active=null;send(503,{error:'Не удалось запустить Python. Задайте PGE_PYTHON с установленным power-grid-env.'});});
  child.on('close',code=>{clearTimeout(timer);if(active===child)active=null;if(failed)return send(504,{error:'Превышен лимит времени или объёма расчёта'});try{const data=JSON.parse(Buffer.concat(output).toString());
    if(agent&&code===0&&data.ok){
      if(action==='tasks')return send(200,{ok:true,tasks:data.tasks});
      episodes.set(episodeId,{state:data.private_state,updated:Date.now()});
      return send(200,['reset','observe'].includes(action)?{ok:true,session_id:episodeId,observation:data.public,...(action==='observe'?{measurements:data.measurements,journal:data.journal,terminal:data.terminal}:{})}:{ok:true,session_id:episodeId,response:data.public});
    }
    send(code===0&&data.ok?200:422,agent?{ok:false,error:data.error||'Agent worker failed'}:data);}catch{send(503,{error:'Python runtime недоступен: '+stderr});}});
  child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify(workerRequest));
}
const server=http.createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,'http://localhost');if(url.pathname==='/api/health'){
 const origin=req.headers.origin;
 if(![`localhost:${port}`,`127.0.0.1:${port}`].includes(req.headers.host)||origin&&!['http://localhost:'+port,'http://127.0.0.1:'+port].includes(origin)){res.writeHead(403);return res.end('Local same-origin requests only');}
 if(req.method!=='GET'){res.writeHead(405,{Allow:'GET'});return res.end();}
 res.writeHead(200,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
 return res.end(JSON.stringify({application:'power-grid-studio',api_version:1,local_only:true,server_ready:true,solver_qualified:false,agent_tools:true,training_jobs:false,model_connections:false,model_exchange:true}));
 }if(['/api/exchange/import','/api/exchange/export','/api/exchange/evaluate','/api/editor/validate','/api/editor/solve','/api/editor/export','/api/agent/tasks','/api/agent/reset','/api/agent/tool','/api/agent/close','/api/agent/observe'].includes(url.pathname))return await api(req,res,url);const name=decodeURIComponent(url.pathname);
    const relative=name==='/'?'index.html':name.replace(/^\/+/, '');const file=path.resolve(root,relative);
    if(!file.startsWith(root+path.sep)||relative.split('/').some(p=>p.startsWith('.'))){res.writeHead(403);return res.end('Forbidden');}
    if(!['GET','HEAD'].includes(req.method)){res.writeHead(405,{'Allow':'GET, HEAD'});return res.end();}
    if(!(await stat(file)).isFile()){res.writeHead(404);return res.end('Not found');}
    const data=await readFile(file);res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream','Content-Length':data.length,'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin'});res.end(req.method==='HEAD'?undefined:data);
  }catch(error){res.writeHead(error instanceof URIError?400:404);res.end(error instanceof URIError?'Bad request':'Not found');}
});
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>{active?.kill('SIGKILL');server.close(()=>process.exit(0));server.closeAllConnections();});
server.on('error',error=>{console.error(`Preview server: ${error.message}`);process.exitCode=1;});
server.listen(port,'127.0.0.1',()=>console.log(`Power Grid Studio · http://localhost:${port}`));
