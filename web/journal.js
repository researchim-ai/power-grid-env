/* Static article content works without JavaScript; enhancements stay local. */
(() => {
  'use strict';
  const translated=[...document.querySelectorAll('[data-en]')].map(el=>({el,ru:el.innerHTML,en:el.dataset.en}));
  const translatedImages=[...document.querySelectorAll('[data-en-alt]')].map(el=>({el,ru:el.alt,en:el.dataset.enAlt}));
  const language=document.querySelector('#language');
  let locale='ru';
  try{if(localStorage.getItem('pge-language')==='en')locale='en';}catch{}
  function translate(){
    document.documentElement.lang=locale;
    for(const {el,ru,en}of translated)el.innerHTML=locale==='en'?en:ru;
    for(const {el,ru,en}of translatedImages)el.alt=locale==='en'?en:ru;
    if(language){language.textContent=locale==='ru'?'EN':'RU';language.setAttribute('aria-label',locale==='ru'?'Switch to English':'Переключить на русский');}
    updateCount();
  }
  language?.addEventListener('click',()=>{locale=locale==='ru'?'en':'ru';try{localStorage.setItem('pge-language',locale);}catch{}translate();});
  const menu=document.querySelector('#menu'),nav=document.querySelector('#navigation');
  const closeMenu=()=>{nav?.classList.remove('is-open');menu?.setAttribute('aria-expanded','false');};
  menu?.addEventListener('click',()=>{const open=menu.getAttribute('aria-expanded')!=='true';menu.setAttribute('aria-expanded',String(open));nav?.classList.toggle('is-open',open);});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&menu?.getAttribute('aria-expanded')==='true'){closeMenu();menu.focus();}});
  nav?.addEventListener('click',event=>{if(event.target.closest('a'))closeMenu();});
  const filters=[...document.querySelectorAll('[data-filter]')],cards=[...document.querySelectorAll('[data-category]')];
  function updateCount(){const count=document.querySelector('#journal-count');if(count){const visible=cards.filter(el=>!el.hidden).length;count.textContent=locale==='ru'?`Показано материалов: ${visible} из ${cards.length}`:`Showing ${visible} of ${cards.length} articles`;}}
  for(const filter of filters)filter.addEventListener('click',()=>{
    for(const button of filters)button.setAttribute('aria-pressed',String(button===filter));
    for(const card of cards)card.hidden=filter.dataset.filter!=='all'&&card.dataset.category!==filter.dataset.filter;
    updateCount();
  });
  translate();
})();
