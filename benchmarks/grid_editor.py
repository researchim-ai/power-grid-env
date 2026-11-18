"""Bounded local manual editor -> existing balanced RMS motor-start solver.

One editable source equivalent, radial lines/two-winding transformers and motors. Exploratory
single-grid runs, not a numerical qualification or installation approval.
JSON stdin/stdout only; no client-selected filesystem paths or executable code.
"""
import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from power_grid_env.study_spec import StudySpec
from power_grid_env.induction_network import run_induction, validate_induction
from power_grid_env.model import parse_vector_group
from power_grid_env.topology import compile_topology
from power_grid_env.rms_study import transformer_parameters
from power_grid_env.evidence import package_fingerprint
from benchmarks.export_grid_studio import keep_samples, line_flow

ID=r'^[a-zA-Z][a-zA-Z0-9_-]{0,31}$'
class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,strict=True)
class Node(Contract):
    id:str=Field(pattern=ID)
    nominal_ll_v:float|None=Field(default=None,ge=100,le=35000)
    x:float=Field(ge=0,le=2000)
    y:float=Field(ge=0,le=1400)
class Line(Contract):
    id:str=Field(pattern=ID)
    from_node:str=Field(pattern=ID)
    to_node:str=Field(pattern=ID)
    length_m:float=Field(gt=0,le=10000)
    r_ohm_km:float=Field(gt=0,le=20)
    x_ohm_km:float=Field(gt=0,le=20)
    rating_a:float=Field(gt=0,le=10000)
class Transformer(Contract):
    id:str=Field(pattern=ID)
    from_node:str=Field(pattern=ID)
    to_node:str=Field(pattern=ID)
    rated_kva:float=Field(ge=1,le=10000)
    r_percent:float=Field(ge=0,le=20)
    x_percent:float=Field(gt=0,le=40)
    tap_primary:float=Field(ge=.8,le=1.2)
    tap_secondary:float=Field(ge=.8,le=1.2)
    vector_group:Literal['Yy0','Dyn11','Dyn1','Dd0']
class Motor(Contract):
    id:str=Field(pattern=ID)
    node_id:str=Field(pattern=ID)
    rated_kva:float=Field(ge=1,le=1000)
    inertia_s:float=Field(ge=.01,le=20)
    fan_torque_pu:float=Field(ge=0,le=2)
    stator_r_pu:float=Field(ge=.001,le=1)
    stator_x_pu:float=Field(ge=.001,le=2)
    rotor_r_pu:float=Field(ge=.001,le=1)
    rotor_x_pu:float=Field(ge=.001,le=2)
    magnetizing_x_pu:float=Field(ge=.1,le=20)
    start_s:float=Field(ge=0,le=10)
    model:Literal['single_cage','double_cage']='single_cage'
    rotor2_r_pu:float=Field(default=.04,ge=.001,le=1)
    rotor2_x_pu:float=Field(default=.15,ge=.001,le=2)
    thermal_enabled:bool=False
    thermal_tau_s:float=Field(default=5.,ge=.01,le=10000)
    thermal_trip:float=Field(default=10.,ge=.01,le=1000)
class Load(Contract):
    id:str=Field(pattern=ID)
    node_id:str=Field(pattern=ID)
    p_kw:float=Field(ge=0,le=2000)
    q_kvar:float=Field(ge=0,le=2000)
    model:Literal['constant_z','constant_pq','constant_i','zip']='constant_z'
    zip_z:float=Field(default=.3,ge=0,le=1)
    zip_i:float=Field(default=.3,ge=0,le=1)
    zip_p:float=Field(default=.4,ge=0,le=1)
    zip_zq:float=Field(default=.3,ge=0,le=1)
    zip_iq:float=Field(default=.3,ge=0,le=1)
    zip_pq:float=Field(default=.4,ge=0,le=1)
    vmin_pu:float=Field(default=.1,gt=0,le=1)
    vmax_pu:float=Field(default=1.5,ge=1,le=2)
    @model_validator(mode='after')
    def weights(self):
        if self.model=='zip' and (abs(self.zip_z+self.zip_i+self.zip_p-1)>1e-9 or abs(self.zip_zq+self.zip_iq+self.zip_pq-1)>1e-9):raise ValueError('ZIP P and Q weights must separately sum to1')
        return self
class Generator(Contract):
    id:str=Field(pattern=ID)
    node_id:str=Field(pattern=ID)
    mode:Literal['pq','pv']='pq'
    rated_kva:float=Field(default=100.,ge=1,le=10000)
    p_kw:float=Field(default=50.,ge=0,le=10000)
    q_kvar:float=Field(default=0.,ge=-10000,le=10000)
    v_set_pu:float=Field(default=1.,ge=.9,le=1.1)
    q_min_kvar:float=Field(default=-40.,ge=-10000,le=10000)
    q_max_kvar:float=Field(default=40.,ge=-10000,le=10000)
    @model_validator(mode='after')
    def capability(self):
        if self.p_kw>self.rated_kva or self.q_min_kvar>self.q_max_kvar:raise ValueError('Invalid generator P/S or Q limits')
        if self.mode=='pq' and math.hypot(self.p_kw,self.q_kvar)>self.rated_kva+1e-9:raise ValueError('Generator PQ exceeds nameplate kVA')
        if self.mode=='pv' and max(math.hypot(self.p_kw,q) for q in (self.q_min_kvar,self.q_max_kvar))>self.rated_kva+1e-9:raise ValueError('Generator Q limits exceed nameplate kVA')
        return self
class Event(Contract):
    time_s:float=Field(gt=0,le=10)
    kind:Literal['motor_open','motor_close','source_voltage','load_torque_scale']
    equipment_id:str=Field(pattern=ID)
    value:float|None=Field(default=None,ge=0,le=2)
class Switch(Contract):
    id:str=Field(pattern=ID)
    from_node:str=Field(pattern=ID)
    to_node:str=Field(pattern=ID)
    kind:Literal['breaker','disconnector']='breaker'
    closed:bool=True
    r_ohm:float=Field(default=.0001,ge=.000001,le=1)
    x_ohm:float=Field(default=.00001,ge=0,le=1)
    rated_current_a:float=Field(default=400.,gt=0,le=100000)
    interrupt_current_a:float=Field(default=25000.,gt=0,le=1000000)
    operate_time_s:float=Field(default=.05,gt=0,le=10)
class Fault(Contract):
    node_id:str=Field(default='',max_length=32)
    r_ohm:float=Field(default=.001,ge=.00001,le=100)
    x_ohm:float=Field(default=0.,ge=0,le=100)
class Draft(Contract):
    schema_version:Literal['power-grid-env.grid-editor/v1','power-grid-env.grid-editor/v2','power-grid-env.grid-editor/v3','power-grid-env.grid-editor/v4','power-grid-env.grid-editor/v5']=Field(alias='schema')
    nominal_ll_v:float=Field(ge=100,le=35000)
    source_node:str=Field(pattern=ID)
    source_r_ohm:float=Field(ge=.0001,le=10)
    source_x_ohm:float=Field(ge=.0001,le=10)
    duration_s:float=Field(ge=.1,le=10)
    step_s:float=Field(ge=.001,le=.0025)
    nodes:list[Node]=Field(min_length=1,max_length=16)
    lines:list[Line]=Field(max_length=15)
    motors:list[Motor]=Field(min_length=0,max_length=16)
    loads:list[Load]=Field(default_factory=list,max_length=16)
    events:list[Event]=Field(default_factory=list,max_length=64)
    transformers:list[Transformer]=Field(default_factory=list,max_length=8)
    generators:list[Generator]=Field(default_factory=list,max_length=8)
    switches:list[Switch]=Field(default_factory=list,max_length=24)
    fault:Fault=Field(default_factory=Fault)
    profile:Literal['lv_motor_start','multivoltage_motor_start','passive_power_flow','passive_3ph_fault','ac_power_flow']='lv_motor_start'
    @model_validator(mode='after')
    def radial(self):
        ids=[v.id for group in (self.nodes,self.lines,self.transformers,self.switches,self.motors,self.loads,self.generators) for v in group]
        if len(set(ids))!=len(ids) or (set(ids)&{'source','editor-fault','vl0','constructor','prototype','__proto__'} or any(i.startswith('vl-') for i in ids)):raise ValueError('IDs must be unique and not reserved')
        nodes={n.id for n in self.nodes}
        if self.source_node not in nodes:raise ValueError('Source node is missing')
        voltage={n.id:n.nominal_ll_v or self.nominal_ll_v for n in self.nodes}
        if voltage[self.source_node]!=self.nominal_ll_v:raise ValueError('Source voltage must equal its node nominal voltage')
        if self.profile=='lv_motor_start' and (self.transformers or any(v>1000 for v in voltage.values())):raise ValueError('Select multivoltage_motor_start profile for transformers or MV nodes')
        steady=self.profile in ('passive_power_flow','passive_3ph_fault','ac_power_flow')
        if steady:
            if self.motors:raise ValueError('Passive PF/fault profile does not model motors; remove them explicitly, no automatic equivalent')
            if self.events:raise ValueError('Time commands belong to RMS; remove them explicitly for static snapshot')
            if self.profile=='passive_3ph_fault' and self.fault.node_id not in nodes:raise ValueError('Select an existing node for three-phase fault')
        elif not self.motors or self.switches:raise ValueError('RMS requires motors and does not yet support network switches in editor')
        if not steady and len(self.lines)+len(self.transformers)!=len(nodes)-1:raise ValueError('Radial network needs exactly nodes-1 lines')
        adj={n:[] for n in nodes}
        for l in [*self.lines,*self.transformers,*self.switches]:
            if l.from_node not in nodes or l.to_node not in nodes or l.from_node==l.to_node:raise ValueError('Line must join two known distinct nodes')
            if isinstance(l,(Line,Switch)) and voltage[l.from_node]!=voltage[l.to_node]:raise ValueError('Cable cannot connect different voltage levels; use transformer')
            adj[l.from_node].append(l.to_node);adj[l.to_node].append(l.from_node)
        seen={self.source_node};queue=[self.source_node]
        for n in queue:
            for o in adj[n]:
                if o not in seen:seen.add(o);queue.append(o)
        if not steady and seen!=nodes:raise ValueError('Network has disconnected nodes or a cycle')
        for m in self.motors:
            if m.node_id not in nodes or m.start_s>=self.duration_s:raise ValueError('Motor node/start time outside study')
        if self.profile!='ac_power_flow' and (self.generators or any(l.model!='constant_z' for l in self.loads)):raise ValueError('Generators and PQ/I/ZIP loads require ac_power_flow; no silent conversion')
        for g in self.generators:
            if g.node_id not in nodes:raise ValueError('Unknown generator node')
        for l in self.loads:
            if l.node_id not in nodes or l.p_kw+l.q_kvar<=0:raise ValueError('Load needs known node and positive nominal demand')
        motors={m.id for m in self.motors};seen_events={(m.start_s,m.id,'switch') for m in self.motors if m.start_s>0}
        for e in self.events:
            if e.time_s>=self.duration_s:raise ValueError('Event outside study horizon')
            if e.equipment_id not in ({'source'} if e.kind=='source_voltage' else motors):raise ValueError('Unknown event target')
            if (e.kind in ('source_voltage','load_torque_scale')) != (e.value is not None):raise ValueError('Voltage/torque events need a scale; switching takes no value')
            key=(e.time_s,e.equipment_id,'switch' if e.kind.startswith('motor_') else e.kind)
            if key in seen_events:raise ValueError('Conflicting coincident commands')
            seen_events.add(key)
        if math.ceil(self.duration_s/self.step_s)>10000:raise ValueError('At most10000 output steps')
        return self

def compile_draft(raw):
    d=Draft.model_validate(raw)
    template=json.loads((Path(__file__).resolve().parents[1]/'cases/network-construction/task-3-motors.json').read_text())['mandatory_cases'][0]['study']
    n=template['network'];n.update(id='manual-grid',name='Manual grid / '+d.profile,connectivity_nodes=[],terminals=[],lines=[],loads=[])
    voltage={v.id:v.nominal_ll_v or d.nominal_ll_v for v in d.nodes}
    n['voltage_levels']=[dict(id='vl-'+str(i),nominal_ll_v=value) for i,value in enumerate(sorted(set(voltage.values())))]
    level={v['nominal_ll_v']:v['id'] for v in n['voltage_levels']}
    n['transformers']=[];n['switches']=[];n['study_faults']=[];n['generators']=[]
    provenance=dict(source='manual-grid-editor',quality='assumed',note='User supplied, not authenticated equipment parameters')
    n['provenance']=provenance
    def terminal(id_,eq,node):
        n['terminals'].append(dict(id=id_,equipment_id=eq,connectivity_node_id=node,phases=['A','B','C']))
        return id_
    for v in d.nodes:n['connectivity_nodes'].append(dict(id=v.id,voltage_level_id=level[voltage[v.id]],phases=['A','B','C'],x=v.x,y=v.y))
    src=n['sources'][0];src.update(ll_v=d.nominal_ll_v,r_ohm=d.source_r_ohm,x_ohm=d.source_x_ohm,terminal_id=terminal('source-terminal','source',d.source_node),provenance=provenance)
    adj={v.id:[] for v in d.nodes}
    for l in d.lines:
        matrix=lambda value:[[value if i==j else 0. for j in range(3)] for i in range(3)]
        n['lines'].append(dict(id=l.id,terminal_from=terminal(l.id+'-from',l.id,l.from_node),terminal_to=terminal(l.id+'-to',l.id,l.to_node),phases=['A','B','C'],length_m=l.length_m,r_ohm=matrix(l.r_ohm_km*l.length_m/1000),x_ohm=matrix(l.x_ohm_km*l.length_m/1000),b_siemens=matrix(0),rating_a=l.rating_a,provenance=provenance))
        adj[l.from_node].append((l.to_node,l));adj[l.to_node].append((l.from_node,l))
    for tr in d.transformers:
        parsed=parse_vector_group(tr.vector_group);connections=parsed.connections
        windings=[]
        for i,(node,tap,connection) in enumerate(zip((tr.from_node,tr.to_node),(tr.tap_primary,tr.tap_secondary),connections)):
            windings.append(dict(terminal_id=terminal(tr.id+'-w'+str(i),tr.id,node),connection=connection,grounding=dict(kind=('solid' if parsed.neutral_solid[i] else 'ungrounded') if connection=='wye' else 'not_applicable'),nominal_ll_v=voltage[node],rated_va=tr.rated_kva*1000,tap_pu=tap))
        n['transformers'].append(dict(id=tr.id,windings=windings,phases=3,vector_group=tr.vector_group,x_hl_pct=tr.x_percent,load_loss_pct=tr.r_percent,no_load_loss_pct=0.,imag_pct=0.,provenance=provenance))
        adj[tr.from_node].append((tr.to_node,tr));adj[tr.to_node].append((tr.from_node,tr))
    for sw in d.switches:
        # Native topology merges Switch objects regardless of representation.
        # Lower this STATIC snapshot explicitly to a finite-Z Line or a cut.
        n['assumptions'].append('Editor static switch snapshot,not protection execution: '+json.dumps(sw.model_dump(mode='json'),sort_keys=True))
        if sw.closed:
            diagonal=lambda value:[[value if i==j else 0. for j in range(3)] for i in range(3)]
            n['lines'].append(dict(id=sw.id,terminal_from=terminal(sw.id+'-from',sw.id,sw.from_node),terminal_to=terminal(sw.id+'-to',sw.id,sw.to_node),phases=['A','B','C'],length_m=1.,r_ohm=diagonal(sw.r_ohm),x_ohm=diagonal(sw.x_ohm),b_siemens=diagonal(0.),rating_a=sw.rated_current_a,provenance=dict(provenance,note='Explicit static switch lowering: finite series R/X,not native Switch node merge')))
    if d.profile=='passive_3ph_fault':n['study_faults']=[dict(id='editor-fault',bus_id=d.fault.node_id,kind='3ph',phases=['A','B','C'],r_ohm=d.fault.r_ohm,x_ohm=d.fault.x_ohm)]
    for l in d.loads:
        n['loads'].append(dict(id=l.id,terminal_id=terminal(l.id+'-terminal',l.id,l.node_id),phases=['A','B','C'],connection='wye',grounding=dict(kind='solid'),model=l.model,vmin_pu=l.vmin_pu,vmax_pu=l.vmax_pu,**({k:getattr(l,k) for k in ('zip_z','zip_i','zip_p','zip_zq','zip_iq','zip_pq')} if l.model=='zip' else {}),p_w={p:l.p_kw*1000/3 for p in 'ABC'},q_var={p:l.q_kvar*1000/3 for p in 'ABC'},provenance=provenance))
    for g in d.generators:
        n['generators'].append(dict(id=g.id,terminal_id=terminal(g.id+'-terminal',g.id,g.node_id),phases=['A','B','C'],mode=g.mode,p_w=g.p_kw*1000,q_var=g.q_kvar*1000,ll_v=voltage[g.node_id],v_set_pu=g.v_set_pu if g.mode=='pv' else None,q_min_var=g.q_min_kvar*1000 if g.mode=='pv' else None,q_max_var=g.q_max_kvar*1000 if g.mode=='pv' else None,v_min_pu=.9,v_max_pu=1.1,provenance=provenance))
    settings=template['induction'];base=copy.deepcopy(settings['motors'][0]);settings['motors']=[];settings['events']=[]
    settings.update(duration_s=d.duration_s,step_s=d.step_s)
    settings['runtime_limits'].update(maximum_network_buses=32,maximum_integration_wall_s=35.,maximum_retained_scalars=2000000,maximum_rhs_evaluations=200000)
    for m in d.motors:
        row=copy.deepcopy(base);row.update({k:getattr(m,k) for k in ('id','node_id','inertia_s','fan_torque_pu','stator_r_pu','stator_x_pu','rotor_r_pu','rotor_x_pu','magnetizing_x_pu')});row.update(rated_va=m.rated_kva*1000,rated_ll_v=voltage[m.node_id],connected=m.start_s==0,model=m.model,rotor2_r_pu=m.rotor2_r_pu if m.model=='double_cage' else None,rotor2_x_pu=m.rotor2_x_pu if m.model=='double_cage' else None,thermal=dict(model='stator_i2_replica',time_constant_s=m.thermal_tau_s,trip_level=m.thermal_trip,initial_level=0.) if m.thermal_enabled else None)
        settings['motors'].append(row)
        if m.start_s>0:settings['events'].append(dict(time_s=m.start_s,kind='motor_close',equipment_id=m.id))
    settings['events'] += [e.model_dump(mode='json') for e in d.events]
    if d.profile in ('passive_power_flow','passive_3ph_fault','ac_power_flow'):
        template.update(study='power_flow',backend='opendss',induction=None)
        study=StudySpec.model_validate(template)
        return d,study,{}
    study=StudySpec.model_validate(template)
    scope=validate_induction(study.network,study.induction)
    if not scope['ok']:raise ValueError('; '.join(scope['errors']))
    parent={d.source_node:None};order=[d.source_node];branches=[]
    for node in order:
        for other,line in adj[node]:
            if other in parent:continue
            parent[other]=node;order.append(other)
            if isinstance(line,Line):
                branches.append(dict(corridor_id=line.id,parent_node=node,child_node=other,length_m=line.length_m,rating_a=line.rating_a,r_ohm=line.r_ohm_km*line.length_m/1000,x_ohm=line.x_ohm_km*line.length_m/1000,kind='line'))
            else:
                branches.append(dict(corridor_id=line.id,parent_node=node,child_node=other,length_m=0.,rating_a=line.rated_kva*1000/(math.sqrt(3)*voltage[node]),kind='transformer',rated_kva=line.rated_kva,primary_node=line.from_node,secondary_node=line.to_node,vector_group=line.vector_group,tap_primary=line.tap_primary,tap_secondary=line.tap_secondary,r_percent=line.r_percent,x_percent=line.x_percent))
    graph=dict(root_node=d.source_node,nominal_ll_v=d.nominal_ll_v,nodes=[dict(node_id=v.id,nominal_ll_v=voltage[v.id]) for v in d.nodes],branches=branches)
    return d,study,graph

def transformer_flow(tr,top,base_va,volts,vbase,parent,nodes):
    params=transformer_parameters(tr,top,base_va);a,b=nodes
    va=volts[a]/vbase[a];vb=volts[b]/vbase[b]
    z=complex(params['r'],params['x']);tap=params['tap']*np.exp(1j*params['phi'])
    ia=(va/abs(tap)**2-vb/np.conj(tap))/z;ib=(vb-va/tap)/z
    sa=va*np.conj(ia)*base_va;sb=vb*np.conj(ib)*base_va
    left,right,send,receive=(a,b,sa,sb) if parent==a else (b,a,sb,sa)
    currents={a:abs(ia)*base_va/(3*vbase[a]),b:abs(ib)*base_va/(3*vbase[b])}
    return dict(p_kw=send.real/1000,q_kvar=send.imag/1000,current_a=currents[left],receiving_current_a=currents[right],receiving_p_kw=-receive.real/1000,receiving_q_kvar=-receive.imag/1000,loss_kw=(sa.real+sb.real)/1000,loading_pct=np.maximum(abs(sa),abs(sb))/tr.windings[0].rated_va*100)

def solve(raw):
    d,study,graph=compile_draft(raw)
    if d.profile in ('passive_power_flow','passive_3ph_fault','ac_power_flow'):
        from benchmarks.editor_steady import solve_steady
        return solve_steady(d,study,raw)
    report=run_induction(study.network,study.induction)
    if not report.get('completed'):raise ValueError('Calculation incomplete: '+str(report.get('status'))+' '+str(report.get('error',report.get('reason',''))))
    c=report['channels'];top=compile_topology(study.network);vbase={v.id:(v.nominal_ll_v or d.nominal_ll_v)/math.sqrt(3) for v in d.nodes};indices=keep_samples(report)
    def values(v):return [round(float(v[i]),6) for i in indices]
    volts={node.id:np.array(c[top.node_to_bus[node.id]+'.v'])*np.exp(1j*np.array(c[top.node_to_bus[node.id]+'.a']))*vbase[node.id] for node in d.nodes}
    channels=dict(source=dict(p_kw=values(np.array(c['source.p_w'])/1000),q_kvar=values(np.array(c['source.q_var'])/1000)),nodes={},branches={},motors={},loads={})
    for node,v in volts.items():channels['nodes'][node]=dict(voltage_pu=values(abs(v)/vbase[node]),angle_deg=values(np.angle(v)*180/math.pi))
    for b in graph['branches']:
        if b['kind']=='line':
            flow=line_flow(volts[b['parent_node']],volts[b['child_node']],b['r_ohm'],b['x_ohm'])
            flow['loading_pct']=flow['current_a']/b['rating_a']*100
        else:
            tr=next(t for t in study.network.transformers if t.id==b['corridor_id'])
            flow=transformer_flow(tr,top,study.network.s_base_va,volts,vbase,b['parent_node'],(b['primary_node'],b['secondary_node']))
        channels['branches'][b['corridor_id']]={k:values(v) for k,v in flow.items()}
    for l in d.loads:
        voltage=abs(volts[l.node_id])/vbase[l.node_id];p=l.p_kw*voltage**2;q=l.q_kvar*voltage**2
        channels['loads'][l.id]=dict(p_kw=values(p),q_kvar=values(q),current_a=values(np.hypot(p,q)*1000/(3*vbase[l.node_id]*np.maximum(voltage,1e-12))),bus_voltage_pu=values(voltage))
    for m in study.induction.motors:
        prefix=m.id+'.';out={k:values(c[prefix+k]) for k in ('connected','speed_pu','current_pu','bus_voltage_pu')}
        out.update(display_speed_pu=[round(c[prefix+'speed_pu'][i],3) for i in indices],current_a=values(np.array(c[prefix+'current_pu'])*m.rated_va/(math.sqrt(3)*m.rated_ll_v)),p_kw=values(np.array(c[prefix+'p_w'])/1000),q_kvar=values(np.array(c[prefix+'q_var'])/1000))
        if m.thermal:out['thermal_level']=values(c[prefix+'thermal_level'])
        channels['motors'][m.id]=out
    study_out=dict(id='manual-start',time_s=[report['time_s'][i] for i in indices],sample_side=[report['sample_side'][i] for i in indices],series=channels,events=[{k:e[k] for k in ('time_s','kind','equipment_id') if k in e} for e in report['event_log']])
    draft_hash=hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema='power-grid-env.grid-studio/v1',profile=d.profile,scope='Live local single-grid balanced multivoltage RMS motor start,constant-Z loads,optional two-cage/thermal; assumed user parameters; not numerical qualification or normative approval',provenance=dict(physics_code_sha256=package_fingerprint(),draft_sha256=draft_hash),designs=[dict(id='manual-grid',graph=graph,loads=[l.model_dump(mode='json') for l in d.loads],motors=[dict(id=m.id,node_id=m.node_id,rated_va=m.rated_va,rated_ll_v=m.rated_ll_v,inertia_s=m.inertia_s) for m in study.induction.motors],studies=[study_out])])

def main():
    try:
        request=json.loads(sys.stdin.buffer.read(262145))
        if request.get('action')=='validate':
            d,s,g=compile_draft(request['draft']);result=dict(valid=True,graph=g,scope=d.profile+': explicit assumed parameters; numerical solver compatibility only')
        elif request.get('action')=='export':
            d,s,g=compile_draft(request['draft']);result=s.model_dump(mode='json')
        elif request.get('action')=='solve':result=solve(request['draft'])
        else:raise ValueError('Unknown action')
        print(json.dumps(dict(ok=True,result=result),allow_nan=False))
    except Exception as exc:
        print(json.dumps(dict(ok=False,error=str(exc)[:6000]),ensure_ascii=False));sys.exit(1)
if __name__=='__main__':main()
