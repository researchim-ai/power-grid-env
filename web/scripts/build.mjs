import {mkdir,readFile,writeFile,copyFile,cp,rm} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const project=path.dirname(root);const plan=await readFile(path.join(project,'plans/CURRENT_STATUS.md'),'utf8');
// Preserve the explicitly dated B15 profile. A newer targeted test subset is
// not comparable to this historical required-suite denominator.
const snapshot=JSON.parse(await readFile(path.join(root,'assets/site-data.json'),'utf8'));
if(!Number.isSafeInteger(snapshot.required_tests)||snapshot.required_tests<=0||!/^\d{4}-\d{2}-\d{2}$/.test(snapshot.snapshot))throw new Error('Invalid historical validation snapshot');
const replay=JSON.parse(await readFile(path.join(root,'assets/grid-replays.json'),'utf8'));
await mkdir(path.join(root,'assets'),{recursive:true});
await writeFile(path.join(root,'assets/site-data.json'),JSON.stringify({...snapshot,grid_studio_recorded_data:true,grid_studio_live_solver:true,grid_studio_solver_requires_local_server:true,grid_editor_profiles:['lv_motor_start','multivoltage_motor_start','passive_power_flow','passive_3ph_fault','ac_power_flow'],grid_editor_two_winding_transformers:true,grid_studio_designs:replay.designs.length,grid_studio_studies:replay.designs.reduce((n,d)=>n+d.studies.length,0)},null,2)+'\n');
await copyFile(path.join(project,'plans/CURRENT_STATUS.md'),path.join(root,'assets/project-status.md'));
await copyFile(path.join(project,'LICENSE'),path.join(root,'assets/LICENSE.txt'));
await rm(path.join(root,'dist'),{recursive:true,force:true});await mkdir(path.join(root,'dist'));
for(const name of ['index.html','styles.css','app.js','grid.html','grid.css','grid.js','grid-model.js','grid-plots.js','editor.html','editor.css','editor.js','editor-model.js','editor-store.js','equipment-catalog.js','steady.html','steady.css','steady.js','steady-model.js','agent-lab.html','agent-lab.css','agent-lab.js','agent-lab-model.js','assets'])await cp(path.join(root,name),path.join(root,'dist',name),{recursive:true});
console.log('Static website built in web/dist. No external dependencies or services.');
