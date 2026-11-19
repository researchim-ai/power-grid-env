import {mkdir,cp,rm} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),out=path.join(root,'dist');
await rm(out,{recursive:true,force:true});await mkdir(out,{recursive:true});
for(const name of ['index.html','studio.css','studio.js','exchange.html','exchange.css','exchange.js','grid.html','grid.css','grid.js','grid-model.js','grid-plots.js','editor.html','editor.css','editor.js','editor-model.js','editor-store.js','equipment-catalog.js','steady.html','steady.css','steady.js','steady-model.js','agent-lab.html','agent-lab.css','agent-lab.js','agent-lab-model.js','assets'])await cp(path.join(root,name),path.join(out,name),{recursive:true});
console.log('Engineering frontend built in studio/dist. Live calculations and agent episodes require the Studio server.');
