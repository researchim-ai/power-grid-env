/* Public recorded signals only. Agent mode returns no arrays beyond current t. */
export function plotDefinition(design,study,selection,metric,{agent=false,index=0}={}){
 const type=selection.type,id=selection.id;
 const labels={speed_pu:'Скорость · pu',current_pu:'Ток · I/Iном',bus_voltage_pu:'Напряжение · pu',p_kw:'Активная P · кВт',q_kvar:'Реактивная Q · kvar',current_a:'Ток · A',loading_pct:'Загрузка · %',loss_kw:'Потери · кВт',voltage_pu:'Напряжение · pu',voltage_v:'Линейное U · V',angle_deg:'Угол · °'};
 let fields,traces=[];
 const add=(name,values,motorId=null)=>{if(!Array.isArray(values))return;traces.push({name,values:agent?values.slice(0,index+1):values,motorId});};
 if(type==='motor'){
  fields=['speed_pu','current_pu','bus_voltage_pu','p_kw','q_kvar'];if(!fields.includes(metric))metric=fields[0];
  for(const m of design.motors)add(m.id.toUpperCase(),study.series.motors[m.id][metric],m.id);
 }else if(type==='branch'){
  const b=design.graph.branches.find(b=>b.corridor_id===id),v=study.series.branches[id];fields=['current_a','p_kw','q_kvar','loading_pct',...(v.loss_kw?['loss_kw']:[])];if(!fields.includes(metric))metric=fields[0];
  add(id.toUpperCase()+' · '+b.parent_node.toUpperCase(),v[metric]);
  if(b.kind==='transformer'&&['current_a','p_kw','q_kvar'].includes(metric))add(id.toUpperCase()+' · '+b.child_node.toUpperCase(),v['receiving_'+metric]);
 }else if(type==='node'){
  const v=study.series.nodes[id],nom=design.graph.nodes.find(n=>n.node_id===id)?.nominal_ll_v??design.graph.nominal_ll_v;fields=['voltage_pu','voltage_v',...(v.angle_deg?['angle_deg']:[])];if(!fields.includes(metric))metric=fields[0];add(id.toUpperCase(),metric==='voltage_v'?v.voltage_pu.map(x=>x*nom):v[metric]);
 }else if(type==='load'){
  fields=['p_kw','q_kvar','current_a','bus_voltage_pu'];if(!fields.includes(metric))metric=fields[0];add(id.toUpperCase(),study.series.loads[id][metric]);
 }else{fields=['p_kw','q_kvar'];if(!fields.includes(metric))metric=fields[0];add('ИСТОЧНИК',study.series.source[metric]);}
 return {metric,options:fields.map(value=>({value,label:labels[value]})),traces,time_s:agent?study.time_s.slice(0,index+1):study.time_s};
}
