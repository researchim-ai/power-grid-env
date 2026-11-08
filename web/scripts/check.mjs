import {readFile,access} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
for(const name of ['app.js','server.mjs','scripts/build.mjs']){const result=spawnSync(process.execPath,['--check',path.join(root,name)],{encoding:'utf8'});if(result.status!==0)throw new Error(result.stderr);}
const html=await readFile(path.join(root,'index.html'),'utf8');const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
if(new Set(ids).size!==ids.length)throw new Error('Duplicate HTML IDs');
for(const [,id] of html.matchAll(/href="#([^"]+)"/g))if(!ids.includes(id))throw new Error('Missing anchor '+id);
for(const [,file] of html.matchAll(/(?:src|href)="\.\/([^"#]+)"/g))await access(path.join(root,file));
for(const file of ['assets/grid-sans-regular.woff','assets/grid-sans-semibold.woff','assets/FONT-LICENSE.txt'])await access(path.join(root,file));
if(/https?:\/\//.test((await readFile(path.join(root,'app.js'),'utf8')).replaceAll('http://www.w3.org/2000/svg','')))throw new Error('External runtime URL found');
console.log(`Checked JavaScript, ${ids.length} unique HTML IDs, navigation anchors and local assets.`);
