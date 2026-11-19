import {readFile,access} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
for(const name of ['app.js','journal.js','site-links.js','site-config.js','server.mjs','scripts/build.mjs','scripts/sync-evidence.mjs']){const result=spawnSync(process.execPath,['--check',path.join(root,name)],{encoding:'utf8'});if(result.status!==0)throw new Error(result.stderr);}
for(const name of ['index.html','blog.html','blog-training.html','blog-physics.html','blog-workbench.html']){
 const html=await readFile(path.join(root,name),'utf8'),ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
 if(new Set(ids).size!==ids.length)throw new Error('Duplicate IDs in '+name);
 for(const [,id]of html.matchAll(/href="#([^"]+)"/g))if(!ids.includes(id))throw new Error('Missing anchor '+id);
 for(const [,file]of html.matchAll(/(?:src|href)="\.\/([^"#]+)"/g))await access(path.join(root,file));
 if(/[\u2190-\u21ff\u27f0-\u27ff\u2900-\u297f]/u.test(html))throw new Error('Arrow glyph in '+name);
 if(/(?:href|src)="(?:\.\/)?(?:editor|steady|grid|agent-lab)\.(?:html|js)|fetch\(['"]\/api/.test(html))throw new Error('Embedded application dependency in '+name);
 console.log(name+': publication links checked');
}
const learning=JSON.parse(await readFile(path.join(root,'assets/learning-experiment.json'),'utf8'));
if(learning.schema!=='power-grid-env.website-learning-experiment/v1'||learning.aggregate_suite_tasks!==30||learning.inference_release!==false||learning.visual_input!==false||learning.first_heldout_test.qwen4b_successes!==5||learning.later_evaluation!=='Previously disclosed regression only; not a new independent test.')throw new Error('Learning snapshot scope differs');
const examples=JSON.parse(await readFile(path.join(root,'assets/learning-examples.json'),'utf8'));
if(examples.schema!=='power-grid-env.blog-learning-examples/v1'||examples.examples.length!==2||examples.examples.some(e=>e.split!=='train'||e.base.success||!e.sft.success))throw new Error('Published illustrations must be selected failed-base/successful-SFT TRAIN cases');
if(new Set(examples.examples.map(e=>e.kind)).size!==2||!examples.examples.some(e=>e.kind==='verify_healthy'&&e.changed_line===null&&e.previews.length===0)||!examples.examples.some(e=>e.kind==='compare_replacements'&&e.previews.some(p=>!p.assessment.measurement_feasible)&&e.previews.some(p=>p.assessment.measurement_feasible)))throw new Error('Blog must show distinct observed decisions, including no edit and rejected hypothetical replacement');
for(const e of examples.examples)for(const name of [e.project_file,e.modified_project_file])await access(path.join(root,'assets',name));
for(const [model,expected]of Object.entries({qwen08b:[0,18,23],qwen4b:[0,21,28]})){
  for(const [index,phase]of ['base','fixed96','decision240'].entries()){
    const rows=Object.values(learning.models[model].stages[phase]);
    if(rows.reduce((n,r)=>n+r.total,0)!==30||rows.reduce((n,r)=>n+r.successes,0)!==expected[index])throw new Error('Learning denominator/result differs');
  }
}

await import('../tests/site-config.test.mjs');
