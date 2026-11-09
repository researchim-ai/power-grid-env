"""Independent restart active-set controller around original pandapower runpp.

Unique generator bus, no remote regulation. Limited generators become fixed
sgen injections for the next solve; wrong-side voltage releases the clamp.
Every trial starts flat; no production equation, tangent or state is used.
"""
import numpy as np


def solve_reference(source,factor,*,release=True):
    import pandapower as pp
    if pp.__version__!='3.5.6':raise ValueError('pandapower 3.5.6 required')
    active={};seen=set();original=pp.from_json_string(source['pandapower_json'])
    if original.gen.bus.duplicated().any():raise ValueError('unique generator buses required for this reference controller')
    for iteration in range(64):
        signature=tuple(sorted(active.items()))
        if signature in seen:raise ValueError('independent reactive active-set cycle')
        seen.add(signature);net=pp.from_json_string(source['pandapower_json'])
        for table in ('load','sgen','storage'):
            for col in ('p_mw','q_mvar'):net[table][col]*=factor
        for idx,q in active.items():
            g=net.gen.loc[idx];net.gen.at[idx,'in_service']=False
            pp.create_sgen(net,int(g.bus),p_mw=float(g.p_mw*g.scaling),q_mvar=q)
        pp.runpp(net,init='flat',enforce_q_lims=not release,trafo_model='pi',calculate_voltage_angles=True,
            voltage_depend_loads=False,distributed_slack=False,tolerance_mva=1e-10,max_iteration=60,numba=True,lightsim2grid=False)
        if not release:return net,{},iteration+1
        updated=dict(active)
        for idx,g in original.gen.iterrows():
            if not g.in_service:continue
            if idx not in active:
                q=float(net.res_gen.at[idx,'q_mvar'])
                if q<g.min_q_mvar-1e-7:updated[idx]=float(g.min_q_mvar)
                elif q>g.max_q_mvar+1e-7:updated[idx]=float(g.max_q_mvar)
            else:
                v=float(net.res_bus.at[int(g.bus),'vm_pu'])
                if (active[idx]==g.max_q_mvar and v>g.vm_pu+1e-9) or (active[idx]==g.min_q_mvar and v<g.vm_pu-1e-9):updated.pop(idx)
        if updated==active:return net,active,iteration+1
        active=updated
    raise ValueError('independent active-set iteration budget exhausted')


def normalize_control_reference(case,net,active,source):
    import pandapower as pp
    from benchmarks.ieee_ac_comparison import normalize_reference
    result=normalize_reference(case,net)
    original=pp.from_json_string(source['pandapower_json']);bybus={int(row[0]):row for row in result['generation_by_bus']}
    lookup=net._pd2ppc_lookups['bus']
    for idx,q in active.items():
        g=original.gen.loc[idx];row=bybus[int(lookup[int(g.bus)])]
        row[1]+=float(g.p_mw*g.scaling);row[2]+=q
    return result
