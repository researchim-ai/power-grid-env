import {plotDefinition} from './grid-plots.js';
import {replayStore} from './editor-store.js';
import {validateBundle,indexAt,pastEvents,motorState,layout,observationBundle} from './grid-model.js';
const $=s=>document.querySelector(s),svg=$('#diagram'),ns='http://www.w3.org/2000/svg';
const colors=['#66d9df','#f2bd6e','#b5a0f6','#c6ef82'];
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const f=(v,d=2)=>Number.isFinite(v)?v.toLocaleString('ru-RU',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
const scenario=id=>({'manual-start':'Пуск ручной схемы','simultaneous':'Одновременный пуск','staged':'Ступенчатый пуск','restart':'Провал и восстановление','snapshot':'Снимок наблюдения'}[id]|| (id.startsWith('open-reclose-')?'Размыкание / повторный пуск · '+id.slice(13).toUpperCase():id));
const eventName=e=>({motor_open:'Отключение',motor_close:'Включение',source_voltage:'Изменение питания',load_torque_scale:'Изменение нагрузки'}[e.kind]||e.kind)+' · '+e.equipment_id.toUpperCase();
const sideName=s=>({initial:'исходное состояние',regular:'отсчёт',pre_event:'до события',post_event:'после события'}[s]||s);
const S={bundle:null,design:null,study:null,index:0,agent:false,playing:false,rate:1,time:0,selection:{type:'motor',id:'m3'},elements:new Map(),box:null,base:null,builtIn:true,last:0};
let toastTimer;
function toast(text){$('#toast').textContent=text;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),3200);}
function pause(){cancelAnimationFrame(S.frame);S.playing=false;$('#play').textContent='▶';$('#play').setAttribute('aria-label','Воспроизвести');}
function path(a,b){const mid=a.x+(b.x-a.x)*.46;return `M ${a.x} ${a.y} H ${mid} V ${b.y} H ${b.x}`;}
const svgStyles=`text{font-family:Arial,sans-serif;fill:#dce7ef}.label{font-size:15px;font-weight:600}.sub{font:12px monospace;fill:#8297a8}.wire{fill:none;stroke:#4c6f7d;stroke-width:3;stroke-linejoin:round}.flow{fill:none;stroke:#77e0df;stroke-width:2;stroke-dasharray:2 14;animation:travel 1.5s linear infinite}.hit{fill:none;stroke:transparent;stroke-width:22;cursor:pointer}.equipment{cursor:pointer;outline:none}.equipment:focus .focus-ring{stroke:#c6ef82;stroke-width:2}.focus-ring{fill:none;stroke:transparent}.selected .focus-ring{stroke:#c6ef82;stroke-width:1.5}.selected .body{fill:#162d32}.body{fill:#0c1923;stroke:#678496;stroke-width:2}.equipment:hover .body{stroke:#b9e4e5}.nodebar{stroke:#d4e2e9;stroke-width:6;stroke-linecap:round}.value{font:15px monospace;fill:#a1bacb}.motor-id{font:600 18px monospace}.gauge{fill:none;stroke-width:3;stroke-linecap:round;transform:rotate(-90deg);transform-box:fill-box;transform-origin:center}.contactor{fill:none;stroke:#78bcbf;stroke-width:2}.caption{font:11px monospace;fill:#8097a9;letter-spacing:1px}.source-label{font:12px monospace;fill:#b4cbcf}@keyframes travel{to{stroke-dashoffset:-48}}@media(prefers-reduced-motion:reduce){.flow{animation:none}}`;
function group(type,id,label,body){return `<g class="equipment" data-key="${type}:${esc(id)}" tabindex="0" role="button" aria-label="${esc(label)}"><title>${esc(label)}</title>${body}</g>`;}
function buildDiagram(){
  const d=S.design,l=layout(d);S.layout=l;S.elements.clear();S.base={x:0,y:0,w:l.width,h:l.height};S.box={...S.base};
  let out=`<defs><style>${svgStyles}</style><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L8 4L0 8Z" fill="#77e0df"/></marker></defs><rect width="${l.width}" height="${l.height}" fill="#0a121b" opacity=".25"/><text class="caption" x="320" y="38" id="svg-caption"></text>`;
  const root=l.nodes.get(d.graph.root_node),sy=root.y;
  if(d.graph.branches.some(b=>b.kind==='transformer'))out+=`<path class="wire" d="M100 ${sy}H${root.x}"/><text class="sub" x="145" y="${sy-28}">THEVENIN / ${f(d.graph.nominal_ll_v,0)} V</text>`;
  else out+=`<path class="wire" d="M100 ${sy}H${root.x}"/><circle cx="176" cy="${sy}" r="21" class="body"/><circle cx="200" cy="${sy}" r="21" class="body"/><text class="sub" x="145" y="${sy-38}">TR / ${d.transformer?f(d.transformer.rating_kva,0)+' kVA':'THEVENIN'}</text><text class="sub" x="145" y="${sy+51}">ЭКВИВАЛЕНТ ${f(d.graph.nominal_ll_v,0)} V</text>`;
  out+=group('source','source','Внешний источник',`<circle class="focus-ring" cx="70" cy="${sy}" r="38"/><circle class="body" cx="70" cy="${sy}" r="29"/><path d="M50 ${sy}Q60 ${sy-22} 70 ${sy}T90 ${sy}" fill="none" stroke="#c6ef82" stroke-width="2"/><text class="label" x="33" y="${sy-51}">СЕТЬ</text><text class="source-label" data-value x="28" y="${sy+60}"></text>`);
  for(const b of d.graph.branches){const a=l.nodes.get(b.parent_node),z=l.nodes.get(b.child_node),wire=path(a,z),mid=a.x+(z.x-a.x)*.46;
    out+=group('branch',b.corridor_id,`${b.kind==='transformer'?'Трансформатор':'Линия'} ${b.corridor_id}`,`<path class="wire" data-wire d="${wire}"/><path class="flow" data-flow d="${wire}"/><path class="hit" d="${wire}"/>${b.kind==='transformer'?`<g><rect x="${mid+23}" y="${z.y-25}" width="66" height="50" fill="#0a121b"/><circle cx="${mid+41}" cy="${z.y}" r="18" class="body"/><circle cx="${mid+61}" cy="${z.y}" r="18" class="body"/></g>`:''}<text class="label" x="${mid+14}" y="${z.y-(b.kind==='transformer'?90:38)}">${esc(b.corridor_id.toUpperCase())}<tspan class="sub"${b.kind==='transformer'?` x="${mid+14}" dy="18"`:''}> · ${b.kind==='transformer'?f(b.rated_kva,0)+' kVA · '+esc(b.vector_group):f(b.length_m,0)+' м'}</tspan></text><text class="value" data-value x="${mid+14}" y="${z.y+(b.kind==='transformer'?55:-17)}"></text>`);}
  for(const m of d.motors){const n=l.nodes.get(m.node_id),p=l.motors.get(m.id),end={x:p.x-38,y:p.y},wire=path(n,end),cx=p.x-83,cy=p.y;
    out+=group('motor',m.id,`Двигатель ${m.id}`,`<path class="wire" data-wire d="${wire}"/><path class="flow" data-flow d="${wire}"/><path class="hit" d="${wire}"/><rect x="${cx-18}" y="${cy-18}" width="36" height="36" fill="#0a121b"/><g class="contactor"><circle cx="${cx-12}" cy="${cy}" r="2"/><circle cx="${cx+12}" cy="${cy}" r="2"/><path data-breaker/></g><circle class="focus-ring" cx="${p.x}" cy="${p.y}" r="47"/><circle class="body" cx="${p.x}" cy="${p.y}" r="29"/><circle class="gauge" data-gauge cx="${p.x}" cy="${p.y}" r="37" stroke="#66d9df" stroke-dasharray="232.48"/><text x="${p.x}" y="${p.y+7}" text-anchor="middle" font-size="23">M</text><text class="motor-id" x="${p.x+53}" y="${p.y-13}">${esc(m.id.toUpperCase())}</text><text class="value" data-value x="${p.x+53}" y="${p.y+12}"></text><text class="sub" data-detail x="${p.x+53}" y="${p.y+34}"></text>`);}
  for(const n of d.graph.nodes){const p=l.nodes.get(n.node_id);
    out+=group('node',n.node_id,`Узел ${n.node_id}`,`<rect class="focus-ring" x="${p.x-16}" y="${p.y-31}" width="32" height="62" rx="6"/><path class="nodebar" d="M${p.x} ${p.y-18}V${p.y+18}"/><text class="label" x="${p.x-9}" y="${p.y-50}">${esc(n.node_id.toUpperCase())}${n.nominal_ll_v!==undefined?` · ${f(n.nominal_ll_v/1000,n.nominal_ll_v>=1000?1:2)} kV`:''}</text><text class="value" data-value x="${p.x-9}" y="${p.y-30}"></text>`);}
  for(const load of (d.loads||[])){const n=l.nodes.get(load.node_id),p=l.loads.get(load.id);out+=group('load',load.id,`Нагрузка ${load.id}`,`<path class="wire" d="${path(n,{x:p.x-30,y:p.y})}"/><path class="body" d="M${p.x-28} ${p.y-25}H${p.x+28}L${p.x} ${p.y+26}Z"/><circle class="focus-ring" cx="${p.x}" cy="${p.y}" r="38"/><text class="label" x="${p.x+42}" y="${p.y-15}">${esc(load.id.toUpperCase())} · Z</text><text class="value" data-value x="${p.x+42}" y="${p.y+10}"></text>`);}
  svg.innerHTML=out;
  for(const el of svg.querySelectorAll('[data-key]'))S.elements.set(el.dataset.key,{root:el,value:el.querySelector('[data-value]'),detail:el.querySelector('[data-detail]'),wire:el.querySelector('[data-wire]'),flow:el.querySelector('[data-flow]'),gauge:el.querySelector('[data-gauge]'),breaker:el.querySelector('[data-breaker]')});
  const mini=$('#minimap');mini.setAttribute('viewBox',`0 0 ${l.width} ${l.height}`);mini.innerHTML=d.graph.branches.map(b=>`<path d="${path(l.nodes.get(b.parent_node),l.nodes.get(b.child_node))}" stroke="#6c939e" stroke-width="9" fill="none"/>`).join('')+[...l.nodes.values()].map(p=>`<circle cx="${p.x}" cy="${p.y}" r="15" fill="#c6ef82"/>`).join('')+'<rect id="mini-window" fill="#66d9df11" stroke="#66d9df" stroke-width="6"/>';
  updateView();
}
function updateView(){if(!S.box)return;svg.setAttribute('viewBox',`${S.box.x} ${S.box.y} ${S.box.w} ${S.box.h}`);$('#zoom-level').textContent=Math.round(S.base.w/S.box.w*100)+'%';const r=$('#mini-window');for(const [k,v]of Object.entries({x:S.box.x,y:S.box.y,width:S.box.w,height:S.box.h}))r.setAttribute(k,v);}
function zoom(factor,point){if(!S.box)return;const b=S.box,nw=b.w*factor;if(nw<S.base.w*.14||nw>S.base.w*3)return;const p=point??{x:b.x+b.w/2,y:b.y+b.h/2};S.box={x:p.x-(p.x-b.x)*factor,y:p.y-(p.y-b.y)*factor,w:nw,h:b.h*factor};updateView();}
function powerFlow(el,p,loading,enabled=true){
  if(!el.wire)return;
  const hot=loading>100,color=!enabled?'#526471':hot?'#f2bd6e':'#5aadb7';
  el.wire.style.stroke=color;el.wire.style.strokeWidth=String(enabled?Math.min(6,2+Math.log10(1+Math.abs(p))):2);el.wire.style.strokeDasharray=enabled?'':'4 6';
  el.flow.style.display=enabled&&Math.abs(p)>.05?'':'none';el.flow.style.stroke=hot?'#f2bd6e':'#77e0df';
  el.flow.style.animationDuration=Math.max(.6,3-Math.log10(1+Math.abs(p))*.8)+'s';el.flow.style.animationDirection=p<0?'reverse':'normal';el.flow.style.animationPlayState=S.playing&&!reduced.matches?'running':'paused';
  el.flow.setAttribute(p<0?'marker-start':'marker-end','url(#arrow)');el.flow.removeAttribute(p<0?'marker-end':'marker-start');
}
function inspector(){
  const {type,id}=S.selection,d=S.design,s=S.study,i=S.index;let values=[],description='',status='',note='',icon='',name=id.toUpperCase();
  if(type==='motor'){
    const m=d.motors.find(m=>m.id===id),v=s.series.motors[id],kind=motorState(s,id,i),open=kind==='disconnected';icon='M';description=`Узел ${m.node_id.toUpperCase()} · ${f(m.rated_va/1000,0)} kVA · ${f(m.rated_ll_v,0)} V`;
    status=open?(v.speed_pu[i]>.05?'Контактор открыт · двигатель вращается':'Контактор открыт'):kind==='at_running_speed'?'Подключён · рабочая скорость':'Подключён · ниже рабочей скорости';
    values=[['Подключение',open?'ОТКЛ':'ВКЛ'],['Скорость',f(v.display_speed_pu[i],3)+' pu'],['Напряжение',f(v.bus_voltage_pu[i],3)+' pu'],['Ток',f(v.current_a[i],1)+' A'],['Активная P',f(v.p_kw[i],2)+' кВт'],['Реактивная Q',f(v.q_kvar[i],2)+' kvar']];
    if(!S.agent&&v.thermal_level)values.push(['Тепловая реплика',f(v.thermal_level[i],3)]);
    note='Рабочая скорость: ≥ 0,900 pu. Открытый контактор имеет приоритет: вращение не означает электрическое подключение.';
  }else if(type==='load'){
    const load=d.loads.find(l=>l.id===id),v=s.series.loads[id];icon='▽';description=`Узел ${load.node_id.toUpperCase()} · постоянная проводимость`;status='Фактическая нагрузка при текущем напряжении';values=[['Активная P',f(v.p_kw[i],2)+' кВт'],['Реактивная Q',f(v.q_kvar[i],2)+' kvar'],['Ток',f(v.current_a[i],1)+' A'],['Напряжение',f(v.bus_voltage_pu[i],3)+' pu'],['Номинальная P',f(load.p_kw,2)+' кВт'],['Номинальная Q',f(load.q_kvar,2)+' kvar']];note='Мощность постоянной проводимости меняется пропорционально U². Это не constant-P нагрузка.';
  }else if(type==='branch'&&d.graph.branches.find(b=>b.corridor_id===id)?.kind==='transformer'){
    const b=d.graph.branches.find(b=>b.corridor_id===id),v=s.series.branches[id],nom=node=>d.graph.nodes.find(n=>n.node_id===node)?.nominal_ll_v??d.graph.nominal_ll_v;icon='◎';description=`${b.primary_node.toUpperCase()} ${f(nom(b.primary_node),0)} V / ${b.secondary_node.toUpperCase()} ${f(nom(b.secondary_node),0)} V · ${b.vector_group}`;status=v.loading_pct[i]>100?'Выше номинальной полной мощности':'В пределах номинальной полной мощности';values=[['Номинал',f(b.rated_kva,0)+' kVA'],['Отдающая P',f(v.p_kw[i],2)+' кВт'],['Приёмная P',f(v.receiving_p_kw[i],2)+' кВт'],['Ток '+b.parent_node.toUpperCase(),f(v.current_a[i],2)+' A'],['Ток '+b.child_node.toUpperCase(),f(v.receiving_current_a[i],2)+' A'],['Потери меди',f(v.loss_kw[i],3)+' кВт'],['Загрузка |S|/Sном',f(v.loading_pct[i],1)+' %'],['Отпайки перв./втор.',f(b.tap_primary,3)+' / '+f(b.tap_secondary,3)]];note='Двухобмоточный симметричный RMS-эквивалент с фиксированными ответвлениями. Нет броска намагничивания,потерь стали или динамического РПН. Загрузка оценивается по большей |S| сторон.';
  }else if(type==='branch'){
    const b=d.graph.branches.find(b=>b.corridor_id===id),v=s.series.branches[id];icon='↗';description=`${b.parent_node.toUpperCase()} → ${b.child_node.toUpperCase()} · ${f(b.length_m,0)} м`;
    status=v.loading_pct[i]>100?'Ток выше длительного паспортного':'Ток в пределах длительного паспортного';
    values=[['Активная P',f(v.p_kw[i],2)+' кВт'],['Реактивная Q',f(v.q_kvar[i],2)+' kvar'],['Ток',f(v.current_a[i],1)+' A'],['I / Iдоп',f(v.loading_pct[i],1)+' %'],['Длительный Iдоп',f(b.rating_a,0)+' A'],['Потери в линии',f(v.loss_kw?.[i],3)+' кВт']];
    note='Мгновенное RMS-превышение Iдоп при пуске не равно срабатыванию защиты. Это не расчёт её выдержки или селективности.';
  }else if(type==='node'){
    const v=s.series.nodes[id].voltage_pu[i],nominal=d.graph.nodes.find(n=>n.node_id===id)?.nominal_ll_v??d.graph.nominal_ll_v;icon='⊥';description='Узел связности · '+f(nominal,0)+' V';status=v<.85?'Значительный провал напряжения':v<.95?'Напряжение снижено':'Под напряжением';
    values=[['Напряжение',f(v,3)+' pu'],['Линейное U',f(v*nominal,1)+' V'],...(s.series.nodes[id].angle_deg?[['Угол относительно источника',f(s.series.nodes[id].angle_deg[i],2)+' °']]:[]),['Двигатели',d.motors.filter(m=>m.node_id===id).map(m=>m.id.toUpperCase()).join(', ')||'—']];note='Отображается симметричный RMS-режим. Цвет — визуальная шкала напряжения, не нормативный вердикт.';
  }else{
    icon='∿';name='ВНЕШНЯЯ СЕТЬ';description=d.graph.branches.some(b=>b.kind==='transformer')?'Источник Тевенина · '+f(d.graph.nominal_ll_v,0)+' V':'Источник за вторичным эквивалентом трансформатора';status='Записанные значения расчёта';values=[['Активная P',f(s.series.source.p_kw[i],2)+' кВт'],['Реактивная Q',f(s.series.source.q_kvar[i],2)+' kvar'],['Номинальное U',f(d.graph.nominal_ll_v,0)+' V']];note='Эквивалент источника 50 Hz. Здесь нет модели намагничивания или броска тока трансформатора.';
  }
  $('#equipment-icon').textContent=icon;$('#selection-type').textContent=({motor:'ДВИГАТЕЛЬ',load:'НАГРУЗКА Z',branch:'КАБЕЛЬНАЯ ЛИНИЯ',node:'УЗЕЛ',source:'ИСТОЧНИК'})[type];if(type==='branch'&&d.graph.branches.find(b=>b.corridor_id===id)?.kind==='transformer')$('#selection-type').textContent='ТРАНСФОРМАТОР';$('#selection-title').textContent=name;$('#selection-description').textContent=description;$('#selection-status').textContent=status;$('#limits-note').textContent=note;
  $('#measurements').innerHTML=values.map(([a,b])=>`<div><dt>${esc(a)}</dt><dd>${esc(b)}</dd></div>`).join('');
}
function events(){
  const s=S.study,i=S.index,visible=S.agent?pastEvents(s,i):s.events,now=s.time_s[i],past=pastEvents(s,i);
  $('#event-counter').textContent=String(past.length).padStart(2,'0');
  $('#event-log').innerHTML=visible.length?visible.map(e=>`<div class="event-row ${past.includes(e)?'':'future'}"><time>${e.time_s.toFixed(3)}</time><span>${esc(eventName(e))}${past.includes(e)?'':' · запланировано'}</span></div>`).join(''):'<p class="subtle">В наблюдаемом префиксе нет коммутаций</p>';
  $('#event-markers').innerHTML=visible.map(e=>{const pre=indexAt(s,e.time_s,'pre_event'),post=indexAt(s,e.time_s);return `<button data-frame="${pre}" class="${i===pre?'active':''}" aria-label="До события ${esc(eventName(e))}">${e.time_s.toFixed(3)} до</button><button data-frame="${post}" class="${i===post?'active':''}">${esc(eventName(e))}</button>`;}).join('');
}
function chart(){
  const s=S.study,plot=plotDefinition(S.design,s,S.selection,$('#metric').value,{agent:S.agent,index:S.index}),metric=plot.metric,n=plot.time_s.length,duration=Math.max(plot.time_s.at(-1),.001);const options=plot.options.map(o=>`<option value="${o.value}">${o.label}</option>`).join('');if($('#metric').dataset.options!==options){$('#metric').innerHTML=options;$('#metric').dataset.options=options;}$('#metric').value=metric;
  let low=0,high=.001;for(const trace of plot.traces)for(let j=0;j<n;j++){const v=trace.values[j];low=Math.min(low,v);high=Math.max(high,v);}
  if(metric==='speed_pu'){low=0;high=Math.max(1.05,high);}else{const pad=(high-low)*.12||.1;high+=pad;if(low<0)low-=pad;}
  const x=t=>43+935*t/duration,y=v=>145-116*(v-low)/(high-low);
  let out='';
  for(let k=0;k<4;k++){const yy=29+k*116/3,value=high-k*(high-low)/3;out+=`<path d="M43 ${yy}H978" stroke="#243544" stroke-width=".6"/><text x="0" y="${yy+3}" fill="#7e94a5" font-size="10" font-family="monospace">${value.toFixed(metric==='p_kw'?0:1)}</text>`;}
  for(let k=0;k<=4;k++){const t=duration*k/4;out+=`<text x="${x(t)}" y="169" text-anchor="middle" fill="#7e94a5" font-size="10" font-family="monospace">${t.toFixed(2)}</text>`;}
  if(metric==='speed_pu')out+=`<path d="M43 ${y(.9)}H978" stroke="#829161" stroke-dasharray="4 5" stroke-width=".8"/>`;
  const visibleEvents=S.agent?pastEvents(s,S.index):s.events;
  for(const e of visibleEvents)out+=`<path d="M${x(e.time_s)} 22V145" stroke="#ae8650" stroke-width=".8" stroke-dasharray="2 4"/>`;
  plot.traces.forEach((trace,j)=>{const active=!trace.motorId||S.selection.id===trace.motorId,series=trace.values;const d=s.time_s.slice(0,n).map((t,i)=>`${i?'L':'M'}${x(t).toFixed(2)} ${y(series[i]).toFixed(2)}`).join(' ');out+=`<path d="${d}" fill="none" stroke="${colors[j%colors.length]}" stroke-width="${active?2:1.2}" opacity="${active?1:.4}"/><circle cx="${x(s.time_s[S.index])}" cy="${y(series[S.index])}" r="3" fill="${colors[j%colors.length]}"/>`;});
  out+=`<path d="M${x(s.time_s[S.index])} 20V145" stroke="#d1e0e8" stroke-dasharray="3 4" stroke-width=".8"/>`;
  $('#chart').setAttribute('aria-label','Осциллограммы: '+plot.traces.map(t=>t.name).join(', ')+' · '+plot.options.find(o=>o.value===metric).label);$('#chart').innerHTML=out;
  const legend=plot.traces.map((trace,j)=>trace.motorId?`<button data-motor="${esc(trace.motorId)}" class="${S.selection.id===trace.motorId?'active':''}" style="--color:${colors[j%colors.length]}"><i></i>${esc(trace.name)}</button>`:`<span style="color:${colors[j%colors.length]};font:11px monospace;margin-right:20px">● ${esc(trace.name)}</span>`).join('');
  if($('#chart-legend').innerHTML!==legend)$('#chart-legend').innerHTML=legend;
}
function render(){
  if(!S.study)return;const s=S.study,i=S.index,t=s.time_s[i];S.time=t;
  $('#time-display').textContent=t.toFixed(4);$('#time-top').textContent=t.toFixed(3)+' s';$('#side-top').textContent=sideName(s.sample_side[i]);$('#scrub').value=i;
  $('#source-power').innerHTML=f(s.series.source.p_kw[i],1)+'<small>кВт</small>';
  const min=Math.min(...S.design.graph.nodes.map(n=>s.series.nodes[n.node_id].voltage_pu[i]));$('#minimum-voltage').innerHTML=f(min,3)+'<small>pu</small>';$('#minimum-voltage').style.color=min<.85?'#f58c7c':min<.95?'#f2bd6e':'#c6ef82';
  $('#connected-count').innerHTML=S.design.motors.filter(m=>s.series.motors[m.id].connected[i]).length+'<small>/ '+S.design.motors.length+'</small>';
  $('#svg-caption').textContent=`${S.agent?'НАБЛЮДЕНИЕ':S.design.id.toUpperCase()} / ${t.toFixed(4)} s / ${s.sample_side[i]}`;
  for(const [key,el]of S.elements){const [kind,id]=key.split(':');el.root.classList.toggle('selected',S.selection.type===kind&&S.selection.id===id);
    if(kind==='load'){const v=s.series.loads[id];el.value.textContent=f(v.p_kw[i],1)+' кВт · '+f(v.current_a[i],1)+' A';}
    if(kind==='source')el.value.textContent=f(s.series.source.p_kw[i],1)+' кВт';
    if(kind==='node'){const v=s.series.nodes[id].voltage_pu[i];el.value.textContent=f(v,3)+' pu';el.root.querySelector('.nodebar').style.stroke=v<.85?'#f58c7c':v<.95?'#f2bd6e':'#9fdde0';}
    if(kind==='branch'){const v=s.series.branches[id];el.value.textContent=f(v.p_kw[i],1)+' кВт · '+f(v.loading_pct[i],0)+'%';powerFlow(el,v.p_kw[i],v.loading_pct[i]);}
    if(kind==='motor'){const v=s.series.motors[id],p=S.layout.motors.get(id),on=v.connected[i]===1;el.value.textContent=f(v.display_speed_pu[i],3)+' pu';el.detail.textContent=(on?'ON · ':'OFF · ')+f(v.current_a[i],0)+' A';el.detail.style.fill=on?'#94b0c3':'#f2bd6e';el.gauge.setAttribute('stroke-dashoffset',String(232.48*(1-Math.min(1,Math.max(0,v.speed_pu[i])))));el.gauge.style.stroke=on?'#66d9df':'#b5a0f6';const cx=p.x-83;el.breaker.setAttribute('d',on?`M${cx-12} ${p.y}H${cx+12}`:`M${cx-12} ${p.y}L${cx+10} ${p.y-13}`);powerFlow(el,v.p_kw[i],0,on);}
  }
  inspector();events();chart();
}
function setIndex(i){if(!S.study)return;S.index=Math.max(0,Math.min(S.study.time_s.length-1,i));render();}
function chooseStudy(id,initial=false){pause();S.study=S.design.studies.find(s=>s.id===id)||S.design.studies[0];$('#study').value=S.study.id;$('#scrub').max=S.study.time_s.length-1;$('#duration').textContent=S.study.time_s.at(-1).toFixed(3)+' s';$('#sample-caption').textContent=`${S.study.time_s.length} сохранённых отсчётов · без интерполяции`;
  $('#case-title').textContent=S.agent?'Текущее наблюдение':scenario(S.study.id);setIndex(initial?indexAt(S.study,3):0);}
function chooseDesign(id,initial=false){pause();S.design=S.bundle.designs.find(d=>d.id===id)||S.bundle.designs[0];$('#design').value=S.design.id;
  $('#study').innerHTML=S.design.studies.map(s=>`<option value="${esc(s.id)}">${esc(scenario(s.id))}</option>`).join('');
  const v=$('#verdict');v.textContent=!S.builtIn?'Импорт · источник не проверен':S.design.engineering_status==='accepted'?'Проект принят в своём профиле':'Проект отклонён в своём профиле';v.classList.toggle('rejected',S.design.engineering_status==='rejected');
  if(!S.design.motors.some(m=>m.id===S.selection.id))S.selection={type:'motor',id:S.design.motors[0]?.id};
  if(!S.selection.id)S.selection={type:'node',id:S.design.graph.root_node};buildDiagram();chooseStudy(initial?'open-reclose-m3':S.study?.id,initial);}
function load(bundle,builtIn){validateBundle(bundle);pause();S.bundle=bundle;S.builtIn=builtIn;S.agent=false;document.body.classList.remove('agent-mode');$('#agent').setAttribute('aria-pressed','false');$('#record-badge').textContent='RECORDED / RMS';
  $('#design').innerHTML=bundle.designs.map(d=>`<option value="${esc(d.id)}">${esc(d.id.endsWith('star')?'Звезда · 3 двигателя':d.id.endsWith('chain')?'Цепочка · 3 двигателя':d.id)}</option>`).join('');
  $('#design').disabled=false;$('#study').disabled=false;$('#play').disabled=false;$('#error').hidden=true;
  $('#origin').textContent=builtIn?'Проверенный расчётный профиль':'Локальный импорт · без проверки физики';$('#provenance').textContent='PHYSICS / '+String(bundle.provenance?.physics_code_sha256??'не указан');
  chooseDesign(bundle.designs[0].id,true);
}
$('#design').addEventListener('change',e=>chooseDesign(e.target.value));$('#study').addEventListener('change',e=>chooseStudy(e.target.value));
$('#agent').addEventListener('click',()=>{if(!S.study)return;S.agent=!S.agent;document.body.classList.toggle('agent-mode',S.agent);$('#agent').setAttribute('aria-pressed',String(S.agent));$('#record-badge').textContent=S.agent?'OBSERVATION / PAST ONLY':'RECORDED / RMS';$('#case-title').textContent=S.agent?'Текущее наблюдение':scenario(S.study.id);render();});
$('#metric').addEventListener('change',chart);$('#scrub').addEventListener('input',e=>{pause();setIndex(Number(e.target.value));});
$('#previous').addEventListener('click',()=>{pause();setIndex(S.index-1);});$('#next').addEventListener('click',()=>{pause();setIndex(S.index+1);});
$('#event-markers').addEventListener('click',e=>{const b=e.target.closest('[data-frame]');if(b){pause();setIndex(Number(b.dataset.frame));}});
$('#chart-legend').addEventListener('click',e=>{const b=e.target.closest('[data-motor]');if(b){S.selection={type:'motor',id:b.dataset.motor};render();}});
$('#rate').addEventListener('change',e=>{S.rate=Number(e.target.value);});
$('#play').addEventListener('click',()=>{if(!S.study)return;if(S.playing){pause();render();return;}if(S.index===S.study.time_s.length-1)setIndex(0);S.playing=true;S.time=S.study.time_s[S.index];S.last=performance.now();$('#play').textContent='Ⅱ';$('#play').setAttribute('aria-label','Пауза');S.frame=requestAnimationFrame(tick);});
function tick(now){if(!S.playing)return;const next=S.time+Math.min(.1,(now-S.last)/1000)*S.rate;S.last=now;S.time=next;const index=indexAt(S.study,next);if(index!==S.index){S.index=index;render();S.time=next;}if(next>=S.study.time_s.at(-1)){pause();render();return;}S.frame=requestAnimationFrame(tick);}
let drag=null,moved=false;
svg.addEventListener('pointerdown',e=>{if(!S.box||e.button!==0)return;drag={x:e.clientX,y:e.clientY,box:{...S.box},scale:Math.max(S.box.w/svg.clientWidth,S.box.h/svg.clientHeight)};moved=false;});
svg.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>5)moved=true;if(moved){if(!svg.hasPointerCapture(e.pointerId))svg.setPointerCapture(e.pointerId);svg.classList.add('dragging');S.box={...drag.box,x:drag.box.x-dx*drag.scale,y:drag.box.y-dy*drag.scale};updateView();}});
function endDrag(){drag=null;svg.classList.remove('dragging');setTimeout(()=>{moved=false;},0);}svg.addEventListener('pointerup',endDrag);svg.addEventListener('pointercancel',endDrag);
svg.addEventListener('click',e=>{if(moved)return;const el=e.target.closest('[data-key]');if(el){const [type,id]=el.dataset.key.split(':');S.selection={type,id};render();}});
svg.addEventListener('keydown',e=>{const el=e.target.closest('[data-key]');if(el&&(e.key==='Enter'||e.key===' ')){e.preventDefault();const [type,id]=el.dataset.key.split(':');S.selection={type,id};render();}else if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();pause();setIndex(S.index+(e.key==='ArrowRight'?1:-1));}else if(e.key===' '){e.preventDefault();$('#play').click();}});
svg.addEventListener('wheel',e=>{if(!S.box)return;e.preventDefault();const matrix=svg.getScreenCTM();if(!matrix)return;const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(matrix.inverse());zoom(Math.exp(Math.max(-.25,Math.min(.25,e.deltaY*.001))),p);},{passive:false});
$('#zoom-in').addEventListener('click',()=>zoom(.8));$('#zoom-out').addEventListener('click',()=>zoom(1.25));$('#fit').addEventListener('click',()=>{if(S.base){S.box={...S.base};updateView();}});
$('#minimap').addEventListener('click',e=>{if(!S.box)return;const rect=e.currentTarget.getBoundingClientRect(),x=(e.clientX-rect.left)/rect.width*S.base.w,y=(e.clientY-rect.top)/rect.height*S.base.h;S.box.x=x-S.box.w/2;S.box.y=y-S.box.h/2;updateView();});
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1500);}
$('#export-json').addEventListener('click',()=>{if(!S.study)return;pause();render();download(new Blob([JSON.stringify(observationBundle(S.bundle,S.design,S.study,S.index),null,2)],{type:'application/json'}),`grid-observation-${S.study.time_s[S.index].toFixed(4)}-${S.study.sample_side[S.index]}.json`);toast('Сохранён префикс наблюдения. Будущих команд и вердикта в JSON нет.');});
async function picture(png){if(!S.study)return;pause();render();const clone=svg.cloneNode(true);clone.setAttribute('xmlns',ns);clone.setAttribute('viewBox',`0 0 ${S.base.w} ${S.base.h}`);clone.querySelector('rect').setAttribute('opacity','1');const scale=Math.min(2,4096/S.base.w,4096/S.base.h);clone.setAttribute('width',Math.round(S.base.w*scale));clone.setAttribute('height',Math.round(S.base.h*scale));const blob=new Blob([new XMLSerializer().serializeToString(clone)],{type:'image/svg+xml;charset=utf-8'});const name=`grid-${S.study.time_s[S.index].toFixed(4)}-${S.study.sample_side[S.index]}`;if(!png){download(blob,name+'.svg');return;}const url=URL.createObjectURL(blob);try{const image=new Image();image.src=url;await image.decode();const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;canvas.getContext('2d').drawImage(image,0,0);const result=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!result)throw new Error('PNG недоступен');download(result,name+'.png');}finally{URL.revokeObjectURL(url);}}
$('#export-svg').addEventListener('click',()=>picture(false).catch(e=>toast(e.message)));$('#export-png').addEventListener('click',()=>picture(true).catch(e=>toast(e.message)));
$('#import-file').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;try{if(file.size>12_000_000)throw new Error('Предел импорта: 12 MB');const data=JSON.parse(await file.text());validateBundle(data);load(data,false);toast('Локальная запись открыта. Происхождение физики не подтверждено.');}catch(error){toast(error.message);}finally{e.target.value='';}});
document.addEventListener('visibilitychange',()=>{if(document.hidden){pause();if(S.study)render();}});
try{if(new URLSearchParams(location.search).has('manual')){const manual=await replayStore();if(!manual)throw new Error('Нет результата конструктора');validateBundle(manual);load(manual,false);$('#origin').textContent='Расчёт ручной схемы · Python / SciPy';$('#verdict').textContent='Одна сетка · без инженерной квалификации';$('#record-badge').textContent='LOCAL RUN / RMS';$('#footer-scope').textContent='Локальный расчёт ручной схемы · одна сетка · не квалификация';}else{const response=await fetch('./assets/grid-replays.json');if(!response.ok)throw new Error('Не удалось открыть локальную запись');load(await response.json(),true);}}catch(error){$('#error').textContent=error.message+' · Откройте страницу через локальный HTTP-сервер.';$('#error').hidden=false;}
