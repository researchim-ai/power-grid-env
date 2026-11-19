// Public navigation only, without credentials or a solver connection.
export const SITE_FILES=['index.html','blog.html','blog-training.html','blog-physics.html','blog-workbench.html','styles.css','journal.css','app.js','journal.js','site-config.js','site-links.js','site.config.json'];
export const SITE_ASSETS=['mark.svg','grid-sans-regular.woff','grid-sans-semibold.woff','FONT-LICENSE.txt','FONT-NOTICE.txt','LICENSE.txt','site-data.json','learning-experiment.json','project-status.md','learning-examples.json','learning-example-1.project.json','learning-example-1-before.svg','learning-example-1-after.svg','learning-example-2.project.json','learning-example-2-before.svg','learning-example-2-after.svg','learning-example-1-after.project.json','learning-example-2-after.project.json'];
export const STUDIO_PAGES=['index.html','editor.html','grid.html','steady.html','agent-lab.html','exchange.html'];
export function studioBase(value){
 if(value===null||value===undefined||value==='')return null;
 if(typeof value!=='string'||value!==value.trim())throw new Error('Studio URL must be an absolute HTTP(S) URL');
 let url;try{url=new URL(value);}catch{throw new Error('Studio URL must be absolute');}
 if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash)throw new Error('Studio URL requires HTTP(S), without credentials, query or fragment');
 url.pathname=url.pathname.replace(/\/+$/,'')+'/';return url.href;
}
export function publicConfig(value,override){
 if(!value||value.schema!=='power-grid-env.public-site-config/v1'||Object.keys(value).some(k=>!['schema','studio_url'].includes(k)))throw new Error('Invalid public site config');
 return {schema:value.schema,studio_url:studioBase(override===undefined?value.studio_url:override)};
}
