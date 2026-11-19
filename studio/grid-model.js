/* Pure replay contracts. No solver, interpolation, remote calls or learned policy. */
export const SCHEMA='power-grid-env.grid-studio/v1';
const own=(o,k)=>Object.hasOwn(o,k);
const id=v=>typeof v==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(v)&&!['constructor','prototype','__proto__'].includes(v);
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const need=(ok,message)=>{if(!ok)throw new Error(message);};
export function validateBundle(data){
  need(data&&data.schema===SCHEMA,'Неизвестный формат Grid Studio');
  need(Array.isArray(data.designs)&&data.designs.length>0&&data.designs.length<=16,'Нужны 1–16 схем');
  const all=new Set();let numbers=0;
  for(const key of ['physics_code_sha256','source_manifest_sha256'])if(data.provenance&&own(data.provenance,key))need(typeof data.provenance[key]==='string'&&/^[0-9a-f]{64}$/.test(data.provenance[key]),'Неверная provenance SHA256');
  for(const d of data.designs){
    need(id(d.id)&&!all.has(d.id),'Повтор или неверный ID схемы');all.add(d.id);
    if(d.transformer)need(id(d.transformer.id)&&finite(d.transformer.rating_kva)&&d.transformer.rating_kva>0,'Неверный паспорт эквивалента');
    const g=d.graph;need(g&&Array.isArray(g.nodes)&&g.nodes.length>0&&g.nodes.length<=128,'Не более 128 узлов');
    need(finite(g.nominal_ll_v)&&g.nominal_ll_v>0,'Неверное номинальное напряжение');
    const nodes=new Set(),motors=new Set(),branches=new Set(),loads=new Set();
    for(const n of g.nodes){need(id(n.node_id)&&!nodes.has(n.node_id),'Неверный ID узла');nodes.add(n.node_id);if(n.nominal_ll_v!==undefined)need(finite(n.nominal_ll_v)&&n.nominal_ll_v>0,'Неверное напряжение шины');}
    need(nodes.has(g.root_node),'Нет корневого узла');
    need(Array.isArray(g.branches)&&g.branches.length===nodes.size-1,'Поддерживается радиальная связная схема');
    const voltage=node=>g.nodes.find(n=>n.node_id===node)?.nominal_ll_v??g.nominal_ll_v;
    const parent=new Map(),children=new Map([...nodes].map(n=>[n,[]]));
    for(const b of g.branches){
      need(id(b.corridor_id)&&!branches.has(b.corridor_id),'Неверный ID линии');branches.add(b.corridor_id);
      need(nodes.has(b.parent_node)&&nodes.has(b.child_node)&&b.parent_node!==b.child_node&&!parent.has(b.child_node)&&b.child_node!==g.root_node,'Несовместимые связи узлов');
      need(finite(b.rating_a)&&b.rating_a>0&&finite(b.length_m)&&b.length_m>=0,'Нет параметров линии');
      need(b.kind===undefined||['line','transformer'].includes(b.kind),'Неизвестный тип ветви');
      if(b.kind==='transformer'){need(finite(b.rated_kva)&&b.rated_kva>0&&nodes.has(b.primary_node)&&nodes.has(b.secondary_node)&&new Set([b.primary_node,b.secondary_node,b.parent_node,b.child_node]).size===2&&b.primary_node!==b.secondary_node,'Неверный паспорт трансформатора');need(['Yy0','Dyn11','Dyn1','Dd0'].includes(b.vector_group)&&finite(b.tap_primary)&&b.tap_primary>0&&finite(b.tap_secondary)&&b.tap_secondary>0&&finite(b.r_percent)&&b.r_percent>=0&&finite(b.x_percent)&&b.x_percent>0,'Неверные обмотки/импеданс трансформатора');}
      else need(voltage(b.parent_node)===voltage(b.child_node),'Линия между разными напряжениями');
      parent.set(b.child_node,b.parent_node);children.get(b.parent_node).push(b.child_node);
    }
    const seen=new Set();function visit(n){need(!seen.has(n),'Цикл в схеме');seen.add(n);children.get(n).forEach(visit);}visit(g.root_node);
    need(seen.size===nodes.size,'Есть изолированные узлы');
    need(Array.isArray(d.motors)&&d.motors.length<=256,'Не более 256 двигателей');
    for(const m of d.motors){need(id(m.id)&&!motors.has(m.id)&&nodes.has(m.node_id),'Неверное подключение двигателя');motors.add(m.id);need(finite(m.rated_va)&&m.rated_va>0&&finite(m.rated_ll_v)&&m.rated_ll_v>0,'Нет номинальных данных двигателя');need(m.rated_ll_v===voltage(m.node_id),'Номинал двигателя не совпадает с шиной');}
    if(d.loads!==undefined){need(Array.isArray(d.loads)&&d.loads.length<=256,'Не более256 нагрузок');for(const l of d.loads){need(id(l.id)&&!loads.has(l.id)&&!motors.has(l.id)&&nodes.has(l.node_id),'Неверная нагрузка');need(finite(l.p_kw)&&l.p_kw>=0&&finite(l.q_kvar)&&l.q_kvar>=0&&l.p_kw+l.q_kvar>0,'Нет номинальной нагрузки');loads.add(l.id);}}
    need(Array.isArray(d.studies)&&d.studies.length>0&&d.studies.length<=32,'Нужны 1–32 записи расчёта');
    const cases=new Set();
    for(const s of d.studies){
      need(id(s.id)&&!cases.has(s.id),'Повтор ID сценария');cases.add(s.id);
      need(Array.isArray(s.time_s)&&s.time_s.length>0&&s.time_s.length<=10000,'Неверная временная шкала');
      const n=s.time_s.length;need(Array.isArray(s.sample_side)&&s.sample_side.length===n,'Нет сторон событий');
      for(let i=0;i<n;i++){
        need(finite(s.time_s[i])&&s.time_s[i]>=0&&(i===0||s.time_s[i]>=s.time_s[i-1]),'Неверное время');
        const side=s.sample_side[i];need(['initial','regular','pre_event','post_event'].includes(side),'Неизвестная сторона события');
        if(i===0)need(side==='initial'&&s.time_s[i]===0,'Первая точка должна быть initial в t=0');else need(side!=='initial','Повтор initial');
        if(side==='post_event')need(i>0&&s.sample_side[i-1]==='pre_event'&&s.time_s[i]===s.time_s[i-1],'Нет pre_event');
        if(side==='pre_event'&&i<n-1)need(s.sample_side[i+1]==='post_event'&&s.time_s[i]===s.time_s[i+1],'Нет post_event');
        if(i>0&&s.time_s[i]===s.time_s[i-1])need(side==='post_event'&&s.sample_side[i-1]==='pre_event','Неоднозначные одинаковые времена');
      }
      function channel(v,label){need(Array.isArray(v)&&v.length===n&&v.every(x=>finite(x)&&Math.abs(x)<=1e12),'Неверный канал '+label);numbers+=v.length;need(numbers<=2_000_000,'Слишком много отсчётов');}
      function group(o,required,keys){need(o&&typeof o==='object'&&!Array.isArray(o),'Нет группы каналов');need(Object.keys(o).length===keys.size&&[...keys].every(k=>own(o,k)),'Состав оборудования и каналов различается');for(const key of keys){const item=o[key];need(item&&required.every(f=>own(item,f)),'Нет обязательных сигналов');for(const [field,values]of Object.entries(item))channel(values,key+'.'+field);}}
      need(s.series&&s.series.source,'Нет каналов источника');
      for(const key of ['p_kw','q_kvar'])channel(s.series.source[key],'source.'+key);
      group(s.series.nodes,['voltage_pu'],nodes);
      group(s.series.branches,['p_kw','q_kvar','current_a','loading_pct'],branches);
      for(const b of g.branches.filter(b=>b.kind==='transformer'))need(['receiving_current_a','receiving_p_kw','receiving_q_kvar','loss_kw'].every(k=>own(s.series.branches[b.corridor_id],k)),'Нет каналов обеих сторон трансформатора');
      group(s.series.motors,['connected','speed_pu','display_speed_pu','current_pu','current_a','bus_voltage_pu','p_kw','q_kvar'],motors);
      if(loads.size||s.series.loads)group(s.series.loads,['p_kw','q_kvar','current_a','bus_voltage_pu'],loads);
      for(const m of motors)need(s.series.motors[m].connected.every(v=>v===0||v===1),'connected должен быть 0/1');
      need(Array.isArray(s.events)&&s.events.length<=512,'Неверный журнал событий');
      for(const e of s.events)need(finite(e.time_s)&&e.time_s>=0&&e.time_s<=s.time_s.at(-1)&&typeof e.kind==='string'&&e.kind.length<80&&id(e.equipment_id),'Неверное событие');
    }
  }
  return data;
}
export function indexAt(study,time,side='post_event'){
  const a=study.time_s;let lo=0,hi=a.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(a[mid]<=time+1e-12)lo=mid+1;else hi=mid;}
  let index=Math.max(0,lo-1);
  if(side==='pre_event'&&study.sample_side[index]==='post_event'&&Math.abs(a[index]-time)<1e-10)index--;
  return Math.max(0,index);
}
export function pastEvents(study,index){
  const time=study.time_s[index],pre=study.sample_side[index]==='pre_event';
  return study.events.filter(e=>e.time_s<time-1e-12||(!pre&&e.time_s<=time+1e-12));
}
export function motorState(study,id,index){
  const m=study.series.motors[id];
  return m.connected[index]===0?'disconnected':m.display_speed_pu[index]>=.9?'at_running_speed':'below_running_speed';
}
export function layout(design){
  const g=design.graph,points=new Map(),motorPoints=new Map(),loadPoints=new Map();let leaf=0,maxDepth=0;
  const children=n=>g.branches.filter(b=>b.parent_node===n).map(b=>b.child_node).sort();
  function place(n,depth){
    const slots=[];maxDepth=Math.max(maxDepth,depth);
    for(const m of design.motors.filter(m=>m.node_id===n).sort((a,b)=>a.id.localeCompare(b.id))){const y=145+leaf++*165;motorPoints.set(m.id,{x:320+(depth+1)*260,y});slots.push(y);maxDepth=Math.max(maxDepth,depth+1);}
    for(const l of (design.loads||[]).filter(l=>l.node_id===n).sort((a,b)=>a.id.localeCompare(b.id))){const y=145+leaf++*165;loadPoints.set(l.id,{x:320+(depth+1)*260,y});slots.push(y);maxDepth=Math.max(maxDepth,depth+1);}
    for(const c of children(n))slots.push(place(c,depth+1));
    if(!slots.length)slots.push(145+leaf++*165);
    const y=slots.reduce((a,b)=>a+b,0)/slots.length;points.set(n,{x:320+depth*260,y});return y;
  }
  place(g.root_node,0);
  return {nodes:points,motors:motorPoints,loads:loadPoints,width:Math.max(1080,320+maxDepth*260+180),height:Math.max(600,leaf*165+130)};
}
export function observationBundle(bundle,design,study,index){
  need(Number.isInteger(index)&&index>=0&&index<study.time_s.length,'Неверный отсчёт');
  const slice=o=>Object.fromEntries(Object.entries(o).map(([k,v])=>[k,v.slice(0,index+1)]));
  const graph={root_node:design.graph.root_node,nominal_ll_v:design.graph.nominal_ll_v,
    nodes:design.graph.nodes.map(n=>({node_id:n.node_id,...(n.nominal_ll_v!==undefined?{nominal_ll_v:n.nominal_ll_v}:{}),motor_ids:design.motors.filter(m=>m.node_id===n.node_id).map(m=>m.id)})),
    branches:design.graph.branches.map(b=>Object.fromEntries(['corridor_id','parent_node','child_node','cable_id','length_m','rating_a','r_ohm','x_ohm','kind','rated_kva','primary_node','secondary_node','vector_group','tap_primary','tap_secondary','r_percent','x_percent'].filter(k=>own(b,k)).map(k=>[k,b[k]])))};
  const series={source:slice({p_kw:study.series.source.p_kw,q_kvar:study.series.source.q_kvar}),nodes:{},branches:{},motors:{},...(design.loads?{loads:{}}:{})};
  for(const [kind,fields]of Object.entries({...(design.loads?{loads:['p_kw','q_kvar','current_a','bus_voltage_pu']}:{ }),nodes:['voltage_pu','angle_deg'],branches:['p_kw','q_kvar','current_a','loading_pct','loss_kw','receiving_current_a','receiving_p_kw','receiving_q_kvar'],motors:['connected','speed_pu','display_speed_pu','current_pu','current_a','bus_voltage_pu','p_kw','q_kvar']})){
    for(const [id,signals]of Object.entries(study.series[kind]))series[kind][id]=slice(Object.fromEntries(fields.filter(k=>own(signals,k)).map(k=>[k,signals[k]])));
  }
  return {schema:SCHEMA,scope:'Past-only recorded observation. Static topology/passports, no future events, internal heat state or engineering verdict. Not a live solver.',
    provenance:Object.fromEntries(['source_manifest_sha256','physics_code_sha256'].filter(k=>bundle.provenance?.[k]!==undefined).map(k=>[k,bundle.provenance[k]])),
    designs:[{id:'observation',graph,...(design.loads?{loads:design.loads.map(l=>({id:l.id,node_id:l.node_id,p_kw:l.p_kw,q_kvar:l.q_kvar}))}:{}),...(design.transformer?{transformer:{id:design.transformer.id,rating_kva:design.transformer.rating_kva}}:{}),motors:design.motors.map(m=>Object.fromEntries(['id','node_id','rated_va','rated_ll_v','inertia_s'].filter(k=>own(m,k)).map(k=>[k,m[k]]))),
      studies:[{id:'snapshot',time_s:study.time_s.slice(0,index+1),sample_side:study.sample_side.slice(0,index+1),series,
        events:pastEvents(study,index).map(e=>({time_s:e.time_s,kind:e.kind,equipment_id:e.equipment_id}))}]}]};
}
