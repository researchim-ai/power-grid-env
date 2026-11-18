"""Owner-constrained radial LV construction, catalog sizing and dynamic studies.

One secondary-referred transformer, passive positive-sequence source faults,
fixed nominal-current drop screening and mandatory three-grid DOL studies.
This is preliminary design, not installation certification or selectivity.
"""
import math
from pathlib import Path
from typing import Annotated,Literal
from pydantic import Field,model_validator
from power_grid_env.electrical_sizing import Contract,Installation,Cable,Transformer
from power_grid_env.equipment_catalog import EquipmentCatalog,derive_catalog_passports
from power_grid_env.industrial_dol import IndustrialDOLCase
from power_grid_env.motor_engineering import MotorEngineeringPlan,run_engineering,verify_engineering
from power_grid_env.model import validate_network_rules
from power_grid_env.study_artifacts import encoded,digest,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint
from power_grid_env.induction_network import validate_induction

ID=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'


class Corridor(Contract):
    id:str=Field(pattern=ID)
    node_a:str=Field(min_length=1)
    node_b:str=Field(min_length=1)
    installation:Installation


class NetworkDesignTask(Contract):
    schema_version:Literal['power-grid-env.network-design/v1']='power-grid-env.network-design/v1'
    id:str=Field(pattern=ID)
    catalog:EquipmentCatalog
    corridors:list[Corridor]=Field(min_length=1,max_length=24)
    node_demand_kva:dict[str,Annotated[float,Field(ge=0)]]
    lagging_power_factor:float=Field(gt=0,le=1)
    maximum_projected_drop_percent:float=Field(gt=0,le=20)
    mandatory_cases:list[IndustrialDOLCase]=Field(min_length=1,max_length=8)

    @model_validator(mode='after')
    def profile(self):
        if len({p.id for p in self.corridors})!=len(self.corridors):raise ValueError('unique owner corridor IDs required')
        first=self.mandatory_cases[0].study.network
        errors=validate_network_rules(first)
        if errors:raise ValueError('invalid owner network: '+'; '.join(errors))
        nodes={n.id for n in first.connectivity_nodes}
        if not 2<=len(nodes)<=8 or set(self.node_demand_kva)!=nodes:raise ValueError('two to eight fixed nodes with exact demand coverage required')
        if sum(self.node_demand_kva.values())<=0:raise ValueError('positive connected demand required')
        for route in self.corridors:
            if route.node_a not in nodes or route.node_b not in nodes or route.node_a==route.node_b:raise ValueError('corridor requires distinct known nodes')
            a=route.installation;c=self.catalog
            row=next((r for r in c.air_temperature_factors if r.ambient_c==a.ambient_c),None)
            if a.method!='manufacturer_air_single_circuit' or row is None or row.factor!=c.assumptions.temperature_factor:raise ValueError('exact catalog air installation row required')
            if c.assumptions.grouping_factor.value!=1 or c.assumptions.other_factor.value!=1:raise ValueError('grouped installation not qualified')
            if any(getattr(a,k)!=getattr(c.assumptions,k).value for k in ('temperature_factor','grouping_factor','other_factor')):raise ValueError('installation/catalog derating mismatch')
        for case in self.mandatory_cases:
            s=case.study;n=s.network
            if s.study!='motor_start' or s.induction is None or case.expectation!='within_limits':raise ValueError('mandatory engineering motor_start cases required')
            if n.model_dump(mode='json')!=first.model_dump(mode='json'):raise ValueError('mandatory cases must share owner network')
            if len(n.sources)!=1 or len(n.voltage_levels)!=1 or n.frequency_hz!=50 or n.voltage_levels[0].nominal_ll_v>1000:raise ValueError('single-source single-voltage 50Hz LV profile required')
            if any((n.transformers,n.switches,n.generators,n.inverters,n.storages,n.shunts,n.neutral_grounds,n.study_faults,n.harmonics,n.omissions)):raise ValueError('unsupported construction equipment')
            if any(set(node.phases)!={'A','B','C'} for node in n.connectivity_nodes):raise ValueError('balanced three-phase nodes required')
            src=n.sources[0];voltage=n.voltage_levels[0].nominal_ll_v
            if src.ll_v!=voltage or src.phase_pu is not None or src.participation is not None:raise ValueError('balanced source matching LV required')
            if any(m.rated_ll_v!=voltage or m.node_id not in nodes for m in s.induction.motors):raise ValueError('motor connection/voltage outside fixed owner nodes')
            terminal={t.id:t.connectivity_node_id for t in n.terminals}
            envelope={node:sum(m.rated_va for m in s.induction.motors if m.node_id==node)/1000 for node in nodes}
            for load in n.loads:
                if any(v<0 for v in [*load.p_w.values(),*load.q_var.values()]):raise ValueError('passive lagging loads required')
                envelope[terminal[load.terminal_id]]+=math.hypot(sum(load.p_w.values()),sum(load.q_var.values()))/1000
            if any(self.node_demand_kva[node]<value for node,value in envelope.items()):raise ValueError('demand must cover every local motor nameplate and auxiliary load')
        MotorEngineeringPlan(id=self.id,cases=self.mandatory_cases)
        return self


class BranchSelection(Contract):
    corridor_id:str=Field(pattern=ID)
    cable_id:str=Field(pattern=ID)


class NetworkProposal(Contract):
    transformer_id:str=Field(pattern=ID)
    branches:list[BranchSelection]=Field(min_length=1,max_length=7)

    @model_validator(mode='after')
    def unique_routes(self):
        if len({b.corridor_id for b in self.branches})!=len(self.branches):raise ValueError('duplicate selected corridor')
        self.branches.sort(key=lambda b:b.corridor_id)
        return self


def compile_network(task,proposal):
    task=NetworkDesignTask.model_validate(task.model_dump(mode='json') if isinstance(task,NetworkDesignTask) else task)
    proposal=NetworkProposal.model_validate(proposal.model_dump(mode='json') if isinstance(proposal,NetworkProposal) else proposal)
    data=derive_catalog_passports(task.catalog)
    cables={p['id']:Cable.model_validate(p) for p in data['cables']};transformers={p['id']:Transformer.model_validate(p) for p in data['transformers']}
    if proposal.transformer_id not in transformers:raise ValueError('transformer must belong to owner catalog')
    transformer=transformers[proposal.transformer_id];network=task.mandatory_cases[0].study.network
    voltage=network.voltage_levels[0].nominal_ll_v
    if transformer.secondary_v!=voltage:raise ValueError('transformer secondary differs from fixed LV')
    zb=voltage**2/(1000*transformer.rating_kva)
    zt=zb/100*complex(transformer.resistance_percent,math.sqrt(transformer.impedance_percent**2-transformer.resistance_percent**2))
    if zt.imag<=0:raise ValueError('positive transformer reactance required by dynamic profile')
    terminals={t.id:t.connectivity_node_id for t in network.terminals};root=terminals[network.sources[0].terminal_id]
    routes={r.id:r for r in task.corridors};adj={n:[] for n in task.node_demand_kva};selected={}
    for p in proposal.branches:
        if p.corridor_id not in routes or p.cable_id not in cables:raise ValueError('branch selection outside owner catalog/corridors')
        r=routes[p.corridor_id];c=cables[p.cable_id]
        if c.x_ohm_km<=0:raise ValueError('positive cable reactance required by dynamic profile')
        selected[r.id]=(r,c);adj[r.node_a].append((r.node_b,r.id));adj[r.node_b].append((r.node_a,r.id))
    if len(selected)!=len(adj)-1:raise ValueError('proposal must be a spanning tree over all owner nodes')
    parent={root:None};order=[root];parent_edge={}
    for node in order:
        for other,id_ in sorted(adj[node]):
            if other==parent[node]:continue
            if other in parent:raise ValueError('parallel/cyclic routes unsupported')
            parent[other]=node;parent_edge[other]=id_;order.append(other)
    if len(parent)!=len(adj):raise ValueError('proposal leaves disconnected owner nodes')
    subtree=dict(task.node_demand_kva)
    for node in reversed(order[1:]):subtree[parent[node]]+=subtree[node]
    vph=voltage/math.sqrt(3);pf=complex(task.lagging_power_factor,-math.sqrt(1-task.lagging_power_factor**2))
    total_current=subtree[root]*1000/(math.sqrt(3)*voltage)
    phasor={root:vph-zt*total_current*pf};path={root:zt};rows=[]
    root_fault=vph/abs(zt)
    checks=dict(transformer_loading=subtree[root]<=transformer.rating_kva)
    for node in order[1:]:
        id_=parent_edge[node];r,c=selected[id_];a=r.installation
        z=complex(c.r_operating_ohm_km,c.x_ohm_km)*a.length_m/1000
        current=subtree[node]*1000/(math.sqrt(3)*voltage);ampacity=c.ampacity_a*a.temperature_factor*a.grouping_factor*a.other_factor
        phasor[node]=phasor[parent[node]]-z*current*pf;path[node]=path[parent[node]]+z
        # Maximum passive source duty is at the branch's upstream cable end.
        duty=vph/abs(path[parent[node]]);limit=(c.k_a_sqrt_s_mm2*c.area_mm2)**2
        branch_checks=dict(voltage=c.rated_voltage_v>=voltage,ampacity=current<=ampacity,
            cable_adiabatic=duty**2*a.clearing_s<=limit,breaker_interrupting=duty<=a.breaker_interrupting_a,
            transformer_short_time=a.clearing_s<=transformer.short_time_s and root_fault<=transformer.short_time_a)
        checks.update({id_+'.'+k:v for k,v in branch_checks.items()})
        rows.append(dict(corridor_id=id_,cable_id=c.id,parent_node=parent[node],child_node=node,
            downstream_kva=subtree[node],nominal_current_a=current,derated_ampacity_a=ampacity,
            r_ohm=z.real,x_ohm=z.imag,upstream_fault_a=duty,cable_i2t_a2s=duty**2*a.clearing_s,
            cable_limit_i2t_a2s=limit,checks=branch_checks))
    nodes=[]
    for node in sorted(adj):
        drop=100*(vph-phasor[node].real)/vph;checks[node+'.projected_drop']=drop<=task.maximum_projected_drop_percent
        nodes.append(dict(node_id=node,voltage_real_v=phasor[node].real,voltage_imag_v=phasor[node].imag,
                          projected_drop_percent=drop,source_fault_a=vph/abs(path[node])))
    static=dict(root_node=root,transformer_id=transformer.id,transformer_r_ohm=zt.real,transformer_x_ohm=zt.imag,
                total_demand_kva=subtree[root],branches=rows,nodes=nodes,checks=checks,passes_declared_checks=all(checks.values()),
                scope='nominal fixed-current projected drop including transformer; passive source-only bolted three-phase fault; supplied clearing and rating assumptions; not IEC60909, motor fault contribution, inrush or selectivity')
    cases=[]
    for original in task.mandatory_cases:
        payload=original.model_dump(mode='json');n=payload['study']['network'];old_ids={l['id'] for l in n['lines']}
        n['terminals']=[t for t in n['terminals'] if t['equipment_id'] not in old_ids];n['lines']=[]
        occupied={p['id'] for group in n.values() if isinstance(group,list) for p in group if isinstance(p,dict) and 'id' in p}|{m['id'] for m in payload['study']['induction']['motors']}
        n['sources'][0].update(r_ohm=zt.real,x_ohm=zt.imag,r0_ohm=None,x0_ohm=None)
        n['sources'][0]['provenance'].update(source=transformer.source.reference,quality='derived',note=transformer.source.conditions)
        for row in sorted(rows,key=lambda r:r['corridor_id']):
            id_=row['corridor_id'];r,c=selected[id_];names=[id_,'nd-'+id_+'-from','nd-'+id_+'-to']
            if occupied.intersection(names):raise ValueError('generated equipment/terminal ID collides with owner equipment')
            occupied.update(names)
            for name,node in zip(names[1:],(row['parent_node'],row['child_node'])):n['terminals'].append(dict(id=name,equipment_id=id_,connectivity_node_id=node,phases=['A','B','C']))
            matrix=lambda v:[[v if i==j else 0. for j in range(3)] for i in range(3)]
            n['lines'].append(dict(id=id_,terminal_from=names[1],terminal_to=names[2],phases=['A','B','C'],length_m=r.installation.length_m,
                r_ohm=matrix(row['r_ohm']),x_ohm=matrix(row['x_ohm']),b_siemens=matrix(0.),rating_a=row['derated_ampacity_a'],
                provenance=dict(source=c.source.reference,quality='derived',note=c.source.conditions)))
        payload['parameter_provenance']+='\nConstructed from owner corridors and catalog '+task.catalog.id+'; secondary-referred transformer, no inrush.'
        settings=payload['study']['induction']
        for _ in range(16):
            if settings['step_s']<=1/(8*n['frequency_hz']):break
            settings['step_s']/=2
        else:raise ValueError('audit base grid exceeds bounded refinement')
        case=IndustrialDOLCase.model_validate(payload)
        errors=validate_network_rules(case.study.network)
        if errors:raise ValueError('compiled network invalid: '+'; '.join(errors))
        cases.append(case)
    plan=MotorEngineeringPlan(id=task.id,cases=cases,per_run_timeout_s=60,total_timeout_s=600)
    for case in plan.cases:
        for level in range(3):
            settings=case.study.induction.model_copy(deep=True);settings.step_s/=2**level
            scope=validate_induction(case.study.network,settings)
            if not scope['ok']:raise ValueError('compiled dynamic scope invalid: '+'; '.join(scope['errors']))
    compilation=dict(schema='power-grid-env.network-compilation/v1',task_sha256=digest(encoded(task.model_dump(mode='json'))),
        proposal_sha256=digest(encoded(proposal.model_dump(mode='json'))),plan_sha256=digest(encoded(plan.model_dump(mode='json'))),
        catalog_derivation=data,static=static,manufacturer_authenticated=False,normative_approval=False)
    encoded(compilation)
    return task,proposal,plan,compilation


def verdict(task,proposal,compilation,dynamic):
    complete=bool(dynamic['completed']);qualified=complete and all(c['numerically_qualified'] for c in dynamic['cases'])
    accepted=bool(qualified and dynamic['engineering_acceptable'] and compilation['static']['passes_declared_checks'])
    return dict(schema='power-grid-env.network-design-result/v1',task_sha256=digest(encoded(task.model_dump(mode='json'))),
        proposal_sha256=digest(encoded(proposal.model_dump(mode='json'))),completed=complete,numerically_qualified=qualified,
        static_checks_passed=compilation['static']['passes_declared_checks'],dynamic_checks_passed=dynamic['engineering_acceptable'],
        meets_declared_requirements=accepted,status='incomplete' if not complete else 'numerically_unqualified' if not qualified else 'accepted' if accepted else 'rejected',
        reward=1. if accepted else 0. if qualified else None,code_sha256=dynamic['producer_sha256'],
        normative_approval=False,scope='owner-constrained radial LV construction; catalog facts plus explicit assumptions; static screening and all mandatory three-grid single-cage motor studies')


def run_network_design(task,proposal,out,*,cancelled=None):
    task,proposal,plan,compilation=compile_network(task,proposal)
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for name,data in [('task',task.model_dump(mode='json')),('proposal',proposal.model_dump(mode='json')),('plan',plan.model_dump(mode='json')),('compilation',compilation)]:
        (root/(name+'.json')).write_bytes(encoded(data))
    dynamic=run_engineering(plan,root/'studies',cancelled=cancelled)
    result=verdict(task,proposal,compilation,dynamic);(root/'result.json').write_bytes(encoded(result));seal(root);return result


def verify_network_design(root,*,expected_task=None,expected_proposal=None,expected_producer=None):
    root=Path(root);anchor=verify(root)
    task,proposal,plan,compilation=compile_network(read_json(root/'task.json'),read_json(root/'proposal.json'))
    if expected_task is not None and NetworkDesignTask.model_validate(expected_task)!=task:raise ValueError('owner task differs')
    if expected_proposal is not None and NetworkProposal.model_validate(expected_proposal)!=proposal:raise ValueError('submitted proposal differs')
    if read_json(root/'plan.json')!=plan.model_dump(mode='json') or read_json(root/'compilation.json')!=compilation:raise ValueError('network compilation differs')
    if read_json(root/'studies'/'input.json')!=plan.model_dump(mode='json'):raise ValueError('mandatory studies differ')
    audit=verify_engineering(root/'studies',expected_producer=expected_producer);result=verdict(task,proposal,compilation,read_json(root/'studies'/'summary.json'))
    if read_json(root/'result.json')!=result or verify(root)!=anchor:raise ValueError('network design verdict/evidence differs')
    checked=dict(verified=True,manifest_sha256=anchor,owner_task_anchored=expected_task is not None,submitted_proposal_anchored=expected_proposal is not None,**result)
    if expected_producer is not None:checked.update(auditor_code_sha256=audit['auditor_code_sha256'],explicit_revision_pin=True)
    return checked
