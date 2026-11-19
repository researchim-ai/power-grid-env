// Local preview. Production hosting needs only web/dist, without Node or Python.
import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {SITE_FILES,SITE_ASSETS,publicConfig} from './site-config.js';
const root=path.dirname(fileURLToPath(import.meta.url));
const args=process.argv.slice(2),index=args.indexOf('--port');
const port=Number(index>=0?args[index+1]:process.env.PORT||5173);
if(!Number.isInteger(port)||port<1||port>65535)throw new Error('Expected a port between 1 and 65535');
const config=publicConfig(JSON.parse(await readFile(path.join(root,'site.config.json'),'utf8')),process.env.PGE_STUDIO_URL);
const allowed=new Set([...SITE_FILES,...SITE_ASSETS.map(n=>'assets/'+n)]);
const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.svg':'image/svg+xml','.woff':'font/woff','.txt':'text/plain; charset=utf-8','.md':'text/plain; charset=utf-8'};
const server=http.createServer(async(req,res)=>{
 try{
  const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname),relative=name==='/'?'index.html':name.replace(/^\/+/, '');
  if(!allowed.has(relative)){res.writeHead(404);return res.end('Not found');}
  if(!['GET','HEAD'].includes(req.method)){res.writeHead(405,{Allow:'GET, HEAD'});return res.end();}
  const data=relative==='site.config.json'?Buffer.from(JSON.stringify(config)):await readFile(path.join(root,relative));
  res.writeHead(200,{'Content-Type':types[path.extname(relative)]||'application/octet-stream','Content-Length':data.length,'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin'});
  res.end(req.method==='HEAD'?undefined:data);
 }catch(error){res.writeHead(error instanceof URIError?400:404);res.end(error instanceof URIError?'Bad request':'Not found');}
});
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>{server.close(()=>process.exit(0));server.closeAllConnections();});
server.on('error',error=>{console.error(error.message);process.exitCode=1;});
server.listen(port,'127.0.0.1',()=>console.log(`power-grid-env website · http://localhost:${port}`));
