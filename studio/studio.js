const status=document.querySelector('#server-status'),refresh=document.querySelector('#refresh-status');
async function checkServer(){
 refresh.disabled=true;status.textContent='Проверяем соединение…';status.dataset.state='checking';
 try{
  const response=await fetch('./api/health',{cache:'no-store',signal:AbortSignal.timeout(5000)});
  if(!response.ok)throw new Error('Server response');
  const data=await response.json();if(data.application!=='power-grid-studio'||data.server_ready!==true)throw new Error('Wrong application');
  status.textContent='Сервер доступен';status.dataset.state='ready';
 }catch{status.textContent='Сервер Studio недоступен. Запустите приложение из каталога studio. Просмотр записей не требует расчётного API.';status.dataset.state='unavailable';}
 finally{refresh.disabled=false;}
}
refresh.addEventListener('click',checkServer);checkServer();
