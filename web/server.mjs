import http from 'node:http';
import {readFile,stat} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.dirname(fileURLToPath(import.meta.url));
const args=process.argv.slice(2);const portArg=args.indexOf('--port');
const port=Number(portArg>=0?args[portArg+1]:process.env.PORT||5173);
if(!Number.isInteger(port)||port<1||port>65535)throw new Error('Expected a port between 1 and 65535');
const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.svg':'image/svg+xml','.woff':'font/woff','.txt':'text/plain; charset=utf-8','.md':'text/plain; charset=utf-8'};
const server=http.createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,'http://localhost');const name=decodeURIComponent(url.pathname);
    const relative=name==='/'?'index.html':name.replace(/^\/+/, '');const file=path.resolve(root,relative);
    if(!file.startsWith(root+path.sep)||relative.split('/').some(p=>p.startsWith('.'))){res.writeHead(403);return res.end('Forbidden');}
    if(!['GET','HEAD'].includes(req.method)){res.writeHead(405,{'Allow':'GET, HEAD'});return res.end();}
    if(!(await stat(file)).isFile()){res.writeHead(404);return res.end('Not found');}
    const data=await readFile(file);res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream','Content-Length':data.length,'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin'});res.end(req.method==='HEAD'?undefined:data);
  }catch(error){res.writeHead(error instanceof URIError?400:404);res.end(error instanceof URIError?'Bad request':'Not found');}
});
server.on('error',error=>{console.error(`Preview server: ${error.message}`);process.exitCode=1;});
server.listen(port,'127.0.0.1',()=>console.log(`power-grid-env web → http://localhost:${port}`));
