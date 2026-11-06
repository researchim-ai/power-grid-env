"""Piecewise analytic relay-clock actions in a fixed RMS automation mode.

Pickup/direction/synchronism are hybrid guards, not smoothed equations. Only
continuous clock rates are differentiated here; resets/event saltations remain
separate. Nonlocal measured line ports are retained explicitly in DAE blocks.
"""
import math
import numpy as np
from scipy.sparse import coo_matrix
from power_grid_env.protection import IEC_CURVES


def voltage_buses(bank):
    m=bank.model;buses=set()
    for p in bank.relays:
        if p.kind!='overcurrent' or p.curve=='definite':continue
        if p.measurement_kind=='line':buses.update(m.lines.parameters[p.measurement_id][:2])
        elif p.measurement_kind=='generator':buses.add(int(m.gen_buses[bank.gen_index[p.measurement_id]]))
        elif p.measurement_kind=='source':buses.add(int(m.source_bus))
    return sorted(buses)


def current_directions(bank,p,state,directions,dv):
    m=bank.model;width=directions.shape[1]
    if p.measurement_kind=='line':
        a,b,y,shunt,ll=m.lines.parameters[p.measurement_id]
        return ((dv[a]-dv[b])*y+dv[a]*shunt/2)*m.network.s_base_va/(math.sqrt(3)*ll) if m.lines.closed[p.measurement_id] else np.zeros(width,complex)
    if p.measurement_kind=='source':
        ll=m.topology.buses[m.bus_names[m.source_bus]].nominal_ll_v
        return -m.source_y*dv[m.source_bus]*m.network.s_base_va/(math.sqrt(3)*ll)
    if p.measurement_kind=='generator':
        i=bank.gen_index[p.measurement_id]
        if not m.gen_connected[i]:return np.zeros(width,complex)
        from power_grid_env.network_automation import generator_emf_derivative
        de=np.array([generator_emf_derivative(m,state,directions[:,j])[i] for j in range(width)])
        return (de-dv[m.gen_buses[i]])/m.gen_z[i]*m.gen_passports[i].rated_va/(math.sqrt(3)*m.generators[i].ll_v)
    raise ValueError('unsupported overcurrent measurement port')


def tangent(bank,time,state,directions,voltage,dv):
    result=np.zeros((bank.end-bank.offset,directions.shape[1]))
    if not bank.initialized:return result
    for i,p in enumerate(bank.relays):
        if bank.tripped[i]:continue
        if not bank.active[i]:result[i]=-directions[bank.offset+i]/p.reset_s;continue
        if p.kind!='overcurrent' or p.curve=='definite' or not bank.available[p.id]:continue
        _,current=bank.port(p.measurement_kind,p.measurement_id,state,voltage);value=abs(current)
        if value<=p.pickup:continue
        di=current_directions(bank,p,state,directions,dv);k,alpha=IEC_CURVES[p.curve]
        slope=alpha*(value/p.pickup)**(alpha-1)/(p.time_multiplier_s*k*p.pickup)
        result[i]=slope*(np.conj(current)*di).real/value
    for i,p in enumerate(bank.transfers):
        if not bank.ats_latched[i] and not bank.ats_active[i]:result[len(bank.relays)+i]=-directions[bank.offset+len(bank.relays)+i]/p.detection_delay_s
    return result


def callback(model,base):
    if base is None or not callable(getattr(base,'dae_actions',None)):return None
    bank=model.automation;offset=bank.offset;n=bank.end
    def padded_actions(time,state,u,ctx,voltage_directions=None):
        return np.vstack([base.dae_actions(time,state[:offset],u[:offset],ctx,voltage_directions=voltage_directions),np.zeros((n-offset,u.shape[1]))])
    # This adapter is used only to build the existing local physical blocks.
    def physical(time,state):raise ValueError('use analytic physical column actions')
    physical.dae_actions=padded_actions;physical.jacobian_context=lambda t,x:model.electrical(x)
    def actions(time,state,u,ctx,voltage_directions=None):
        if voltage_directions is None:
            from power_grid_env.dae import injection_product
            dv=model.lu.solve(injection_product(model,state,u))
        else:dv=np.asarray(voltage_directions)
        physical_rows=padded_actions(time,state,u,ctx,voltage_directions=dv)
        physical_rows[offset:]=tangent(bank,time,state,u,ctx[0],dv)
        return physical_rows
    def columns(time,state,cols,ctx):
        from power_grid_env.jacobian_columns import seeds
        _,u=seeds(n,cols);return actions(time,state,u,ctx)
    def evaluate(time,state):
        from power_grid_env.sparse_jacobian import assemble
        return assemble(evaluate,time,state).toarray()
    evaluate.jacobian_kind='piecewise_analytic_network_automation_'+getattr(base,'jacobian_kind','physical')
    evaluate.jacobian_network_response='direct_solve';evaluate.jacobian_context=physical.jacobian_context
    evaluate.jacobian_columns=columns;evaluate.jacobian_actions=actions;evaluate.dae_actions=actions
    evaluate.automation_physical_callback=physical
    return evaluate


def extend_blocks(blocks,model,callback,time,state,check,tick):
    """Sparse nonlocal relay rows A and f_V, without a state-square matrix."""
    bank=model.automation;n=len(state);nodes=len(model.bus_names);voltage=model.electrical(state)[0]
    ar=[];ac=[];av=[];br=[];bc=[];bv=[]
    for first in range(0,n,8):
        check();cols=np.arange(first,min(n,first+8));u=np.zeros((n,len(cols)));u[cols,np.arange(len(cols))]=1.
        values=tangent(bank,time,state,u,voltage,np.zeros((nodes,len(cols)),complex));tick()
        r,c=np.nonzero(values);ar.extend(r+bank.offset);ac.extend(cols[c]);av.extend(values[r,c])
    buses=voltage_buses(bank)
    for first in range(0,2*len(buses),8):
        check();items=[(b,p) for b in buses for p in [0,1]][first:first+8];dv=np.zeros((nodes,len(items)),complex)
        for j,(b,part) in enumerate(items):dv[b,j]=1. if part==0 else 1j
        values=tangent(bank,time,state,np.zeros((n,len(items))),voltage,dv);tick()
        r,c=np.nonzero(values);br.extend(r+bank.offset);bc.extend([items[j][0]+items[j][1]*nodes for j in c]);bv.extend(values[r,c])
    extra=coo_matrix((av,(ar,ac)),shape=(n,n)).tocsr();blocks.A=blocks.A+extra
    blocks.automation_voltage=coo_matrix((bv,(br,bc)),shape=(n,2*nodes)).tocsr()
    blocks.blocks=[(idx,local+extra[idx,:][:,idx].toarray()) for idx,local in blocks.blocks]
    blocks.stored_local_scalar_slots+=extra.nnz+blocks.automation_voltage.nnz
