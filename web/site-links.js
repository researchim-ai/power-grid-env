import {publicConfig,STUDIO_PAGES} from './site-config.js';
// Without an application URL, HTML links point to launch instructions.
// No requests to Studio; no project data leave the application origin.
try{
 const response=await fetch(new URL('./site.config.json',import.meta.url));
 if(response.ok){
  const config=publicConfig(await response.json());
  if(config.studio_url)for(const link of document.querySelectorAll('[data-studio-page]')){
   if(STUDIO_PAGES.includes(link.dataset.studioPage))link.href=new URL(link.dataset.studioPage,config.studio_url).href;
  }
 }
}catch{ /* Launch-instruction links remain usable without config. */ }
