import {readFile,access} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
for(const name of ['app.js','grid.js','grid-model.js','grid-plots.js','editor.js','editor-model.js','editor-store.js','equipment-catalog.js','steady.js','steady-model.js','agent-lab.js','agent-lab-model.js','server.mjs','scripts/build.mjs']){const result=spawnSync(process.execPath,['--check',path.join(root,name)],{encoding:'utf8'});if(result.status!==0)throw new Error(result.stderr);}
for(const name of ['index.html','grid.html','editor.html','steady.html','agent-lab.html']){
  const html=await readFile(path.join(root,name),'utf8'),ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
  if(new Set(ids).size!==ids.length)throw new Error('Duplicate HTML IDs in '+name);
  for(const [,id]of html.matchAll(/href="#([^"]+)"/g))if(!ids.includes(id))throw new Error('Missing anchor '+id);
  for(const [,file]of html.matchAll(/(?:src|href)="\.\/([^"#]+)"/g))await access(path.join(root,file));
  console.log(name+': '+ids.length+' unique IDs, local links checked');
}
for(const file of ['assets/grid-sans-regular.woff','assets/grid-sans-semibold.woff','assets/FONT-LICENSE.txt'])await access(path.join(root,file));
for(const file of ['app.js','grid.js','grid-model.js'])if(/https?:\/\//.test((await readFile(path.join(root,file),'utf8')).replaceAll('http://www.w3.org/2000/svg','')))throw new Error('External runtime URL in '+file);
await import('../tests/grid-model.test.mjs');

await import('../tests/editor-model.test.mjs');

await import('../tests/steady-model.test.mjs');

await import('../tests/agent-lab-model.test.mjs');
