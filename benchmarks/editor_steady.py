"""Local AC/passive PF and finite-Z 3ph fault presentation from native OpenDSS.

No inferred motor equivalent, no IEC60909 correction/peak/asymmetry, no relay
coordination. Switches have an explicit finite series Z, not ideal hidden wires.
"""
import hashlib,json,math
from pathlib import Path
from power_grid_env.backend_opendss import solve_network
from power_grid_env.evidence import package_fingerprint
from power_grid_env.topology import compile_topology


def solve_steady(draft,study,raw):
    n=study.network;top=compile_topology(n)
    if draft.profile=='passive_3ph_fault':
        bus=top.node_to_bus[draft.fault.node_id]
        island=next(i for i in top.islands if bus in i.bus_ids)
        if not island.energized:raise ValueError('Шина КЗ обесточена (deenergized): нет источника, выберите питаемый узел')
    report=solve_network(n,study.options)
    if not report.converged:raise ValueError('OpenDSS '+report.status+': '+'; '.join(report.warnings)[:2000])
    phase={}
    for v in report.voltages:
        if v.phase in 'ABC':phase.setdefault(v.bus_id,{})[v.phase]=dict(voltage_v=abs(v.complex),voltage_pu=abs(v.complex)/v.v_base_ln_v,angle_deg=math.degrees(math.atan2(v.v_imag_v,v.v_real_v)),energized=v.energized,island_id=v.island_id)
    nodes=[]
    for node in draft.nodes:
        bus=top.node_to_bus[node.id];v=phase.get(bus,{})
        if set(v)!=set('ABC'):raise ValueError('Incomplete node-phase report: '+node.id)
        nodes.append(dict(id=node.id,nominal_ll_v=node.nominal_ll_v or draft.nominal_ll_v,x=node.x,y=node.y,phases=v,energized=all(p['energized'] for p in v.values())))
    inventory={}
    for flow in report.flows:
        if flow.phase in 'ABC':inventory.setdefault(flow.equipment_id,{}).setdefault(flow.terminal_index,[]).append(flow)
    def terminal(eq,number):
        rows=inventory.get(eq,{}).get(number,[])
        return dict(p_kw=sum(f.p_into_w for f in rows)/1000,q_kvar=sum(f.q_into_var for f in rows)/1000,current_a=max((abs(f.current) for f in rows),default=0.),phase_current_a={f.phase:abs(f.current) for f in rows}) if rows else None
    by_node={node['id']:node for node in nodes};branches=[]
    for kind,items in [('line',draft.lines),('transformer',draft.transformers),('switch',draft.switches)]:
        for item in items:
            left,right=terminal(item.id,0),terminal(item.id,1)
            energized=by_node[item.from_node]['energized'] and by_node[item.to_node]['energized'] and (kind!='switch' or item.closed)
            if energized and (left is None or right is None):raise ValueError('Missing branch flow: '+item.id)
            rating=(item.rating_a if kind=='line' else item.rated_current_a if kind=='switch' else None)
            loading=(max(left['current_a'],right['current_a'])/rating*100 if rating else max(math.hypot(left['p_kw'],left['q_kvar']),math.hypot(right['p_kw'],right['q_kvar']))/item.rated_kva*100) if energized else None
            row=dict(id=item.id,kind=kind,from_node=item.from_node,to_node=item.to_node,energized=energized,closed=item.closed if kind=='switch' else True,from_terminal=left if energized else None,to_terminal=right if energized else None,loss_kw=(left['p_kw']+right['p_kw']) if energized else None,loading_pct=loading,passport=item.model_dump(mode='json'))
            if kind=='switch':row['interrupting_screen']=dict(carried_current_a=left['current_a'] if energized else None,rating_a=item.interrupt_current_a,exceeds_rating=left['current_a']>item.interrupt_current_a if energized and draft.profile=='passive_3ph_fault' and item.kind=='breaker' else None,scope='carried RMS fault current in this one passive case;not selectivity,peak duty or worst-case qualification')
            branches.append(row)
    load_checks={}
    for l in draft.loads:
        if l.model!='constant_z' and by_node[l.node_id]['energized']:
            values=[v['voltage_pu'] for v in by_node[l.node_id]['phases'].values()]
            if min(values)<l.vmin_pu-1e-8 or max(values)>l.vmax_pu+1e-8:raise ValueError(l.id+': voltage outside declared load-law window; refusing OpenDSS impedance fallback')
        if by_node[l.node_id]['energized']:
            volts=[v['voltage_pu'] for v in by_node[l.node_id]['phases'].values()]
            def law(u,reactive=False):
                if l.model=='constant_z':return u*u
                if l.model=='constant_i':return u
                if l.model=='constant_pq':return 1.
                return (l.zip_zq*u*u+l.zip_iq*u+l.zip_pq) if reactive else (l.zip_z*u*u+l.zip_i*u+l.zip_p)
            expected_p=l.p_kw*sum(law(u) for u in volts)/3;expected_q=l.q_kvar*sum(law(u,True) for u in volts)/3
            actual=terminal(l.id,0)
            if actual is None:raise ValueError('Missing load measurements: '+l.id)
            ep=actual['p_kw']-expected_p;eq=actual['q_kvar']-expected_q
            if abs(ep)>.0001+1e-6*l.p_kw or abs(eq)>.0001+1e-6*l.q_kvar:raise ValueError(l.id+': native load law not reproduced at measured voltage')
            load_checks[l.id]=dict(expected_p_kw=expected_p,expected_q_kvar=expected_q,p_residual_kw=ep,q_residual_kvar=eq)
    generators=[]
    for g in draft.generators:
        actual=terminal(g.id,0);live=by_node[g.node_id]['energized']
        if live and actual is None:raise ValueError('Missing generator measurements: '+g.id)
        if live and any(not .9-1e-8<=v['voltage_pu']<=1.1+1e-8 for v in by_node[g.node_id]['phases'].values()):raise ValueError(g.id+': generator outside0.9–1.1pu window; refusing native fallback')
        injected=dict(actual,p_kw=-actual['p_kw'],q_kvar=-actual['q_kvar']) if actual else None
        if injected and (abs(injected['p_kw']-g.p_kw)>.0001+1e-6*g.p_kw or g.mode=='pq' and abs(injected['q_kvar']-g.q_kvar)>.0001+1e-6*abs(g.q_kvar)):raise ValueError(g.id+': commanded generator P/Q not reproduced')
        if injected and g.mode=='pv' and not g.q_min_kvar-.05<=injected['q_kvar']<=g.q_max_kvar+.05:raise ValueError(g.id+': reactive limits not satisfied')
        if live and g.mode=='pv' and g.id not in report.limited_generators and abs(by_node[g.node_id]['phases']['A']['voltage_pu']-g.v_set_pu)>1e-4:raise ValueError(g.id+': PV target not met without Q-limit evidence')
        generators.append(dict(id=g.id,node_id=g.node_id,passport=g.model_dump(mode='json'),actual=injected,energized=live,q_limited=g.id in report.limited_generators if live and g.mode=='pv' else None,q_at_bound=min(abs(injected['q_kvar']-g.q_min_kvar),abs(injected['q_kvar']-g.q_max_kvar))<=.0001+1e-6*max(abs(g.q_min_kvar),abs(g.q_max_kvar)) if live and g.mode=='pv' else None,voltage_error_pu=by_node[g.node_id]['phases']['A']['voltage_pu']-g.v_set_pu if live and g.mode=='pv' else None,voltage_pu=by_node[g.node_id]['phases']['A']['voltage_pu'] if live else None))
    fault=None
    if draft.profile=='passive_3ph_fault':
        ft=terminal('editor-fault',0)
        if ft is None or set(ft['phase_current_a'])!=set('ABC'):raise ValueError('Incomplete three-phase fault currents')
        fault=dict(node_id=draft.fault.node_id,r_ohm=draft.fault.r_ohm,x_ohm=draft.fault.x_ohm,**ft,scope='source-only passive finite-Z three-phase RMS fault;no motor contribution,IEC60909 corrections,peak or protection actions')
    source=terminal('source',0)
    return dict(schema='power-grid-env.grid-steady/v1',profile=draft.profile,status=report.status,converged=report.converged,backend=dict(name=report.backend_name,version=report.backend_version,iterations=report.iterations),provenance=dict(physics_code_sha256=package_fingerprint(),native_study_sha256=hashlib.sha256(json.dumps(study.model_dump(mode='json'),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),bridge_sha256=hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_name('grid_editor.py').read_bytes()).hexdigest(),draft_sha256=hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()).hexdigest()),nodes=nodes,branches=branches,generators=generators,loads=[dict(id=l.id,node_id=l.node_id,passport=l.model_dump(mode='json'),actual=terminal(l.id,0),law_check=load_checks.get(l.id)) for l in draft.loads],source=dict(node_id=draft.source_node,p_kw=-source['p_kw'],q_kvar=-source['q_kvar']) if source else None,fault=fault,islands=[i.model_dump(mode='json') for i in report.islands],warnings=report.warnings,scope='Static PF or passive3ph fault with explicit finite-Z switches;load laws and static PQ/PV generators only in AC-PF profile. Deenergized islands are displayed separately. No dynamic switching,relay actions,motor equivalent or normative approval.')
