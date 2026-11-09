"""Bounded synchronous-periodic initialization of lossless Gamma networks.

All electrical currents rotate at source frequency; mechanical speeds are
constant. This is a new network operating point, not import of arbitrary RMS
transient states. Thermal equilibrium and positive core conductance are excluded.
"""
import math
import time
import numpy as np
from scipy.optimize import root
from pydantic import BaseModel,ConfigDict,Field


class FluxOperatingPoint(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    method:str=Field(default='synchronous_periodic',pattern='^synchronous_periodic$')
    minimum_speed_pu:float=Field(default=.8,gt=0,lt=1)
    tolerance:float=Field(default=1e-9,gt=0,le=1e-7)
    maximum_residual_evaluations:int=Field(default=3000,ge=1,le=20000)
    maximum_wall_s:float=Field(default=20,gt=0,le=120)


def scales(circuit):
    if circuit.dynamic_end>128:raise ValueError('periodic initialization supports at most 128 physical states')
    if any(m.p.thermal or m.p.core_loss_s for m in circuit.motors):raise ValueError('periodic initialization excludes thermal/core-loss states')
    result=np.ones(circuit.dynamic_end)
    voltage=max(b.nominal_ll_v for b in circuit.topology.buses.values())*math.sqrt(2/3)
    result[:2*len(circuit.L)]=max(1.,voltage)
    for model,(a,speed,_) in zip(circuit.motors,circuit.addresses):
        result[a]=max(1.,voltage/(circuit.wb*min(model.leakage)))
        result[speed]=circuit.wb/model.p.pole_pairs
    return result


def periodic_residual(circuit,state,scale):
    derivative=circuit.rhs(0.,state)[:circuit.dynamic_end].copy()
    # Subtract j omega x from every stationary-frame electrical current pair.
    sections=[slice(0,2*len(circuit.L)),*[a for a,_,_ in circuit.addresses]]
    for a in sections:
        z=circuit.complex_values(state[a]);rotating=1j*circuit.wb*z
        derivative[a.start:a.stop:2]-=rotating.real
        derivative[a.start+1:a.stop:2]-=rotating.imag
    for model,(_,speed,_) in zip(circuit.motors,circuit.addresses):
        if model.p.locked_rotor:derivative[speed]=circuit.wb*state[speed]
        else:
            synchronous=scale[speed]
            torque_scale=max(1.,model.p.fan_nm_per_rad_s2*synchronous**2+model.p.viscous_nm_per_rad_s*synchronous)
            # Normalize torque mismatch, not acceleration: enormous inertia
            # must not conceal a shaft which is still out of equilibrium.
            derivative[speed]*=model.p.inertia_kg_m2*circuit.wb*synchronous/torque_scale
    return derivative/(circuit.wb*scale)


def assess_periodic(circuit,state,options):
    scale=scales(circuit);residual=periodic_residual(circuit,state,scale)
    edge,rows,voltage,_,_,_,kcl=circuit.quantities(0.,state)
    motors={};qualified=True
    for model,(a,speed,_),(current,_,q) in zip(circuit.motors,circuit.addresses,rows):
        w=float(state[speed]);pu=w*model.p.pole_pairs/circuit.wb
        load=model.p.fan_nm_per_rad_s2*w*abs(w)+model.p.viscous_nm_per_rad_s*w
        valid=abs(w)<=1e-10 if model.p.locked_rotor else options.minimum_speed_pu<=pu<=1+1e-9
        qualified=qualified and valid
        motors[model.p.id]=dict(speed_rad_s=w,speed_pu=pu,currents_ab_a=[[float(z.real),float(z.imag)] for z in current],
            torque_nm=q['torque_nm'],load_torque_nm=float(load),external_holding_torque_nm=float(-q['torque_nm']) if model.p.locked_rotor else None)
    maximum=float(max(abs(residual),default=0))
    return dict(qualified=bool(qualified and maximum<=options.tolerance and kcl<=min(options.tolerance,1e-8)),
        maximum_normalized_rotating_residual=maximum,kcl_relative_residual=float(kcl),motors=motors,
        branch_currents_ab_a={key:[float(z.real),float(z.imag)] for key,z in zip(circuit.branch_ids,edge)},
        node_voltage_ab_v={key:[float(voltage[i].real),float(voltage[i].imag)] for key,i in circuit.lookup.items()},
        stability_proven=False,thermal_equilibrium=False,scope='synchronous-periodic balanced lossless-core electrical and shaft equilibrium; locked shafts have external reaction torque')


def initialize_periodic(circuit):
    options=circuit.settings.operating_point;scale=scales(circuit)
    started=time.monotonic();evaluations=0;attempts=[]
    def residual(y):
        nonlocal evaluations
        evaluations+=1
        if evaluations>options.maximum_residual_evaluations:raise ValueError('periodic initialization residual-evaluation budget exhausted')
        if time.monotonic()-started>=options.maximum_wall_s:raise ValueError('periodic initialization wall deadline exceeded')
        state=np.zeros(circuit.dimension);state[:circuit.dynamic_end]=y*scale
        return periodic_residual(circuit,state,scale)
    for guess_speed in [.99,.95,.85]:
        guess=np.zeros(circuit.dynamic_end)
        for model,(_,speed,_) in zip(circuit.motors,circuit.addresses):guess[speed]=0. if model.p.locked_rotor else guess_speed
        answer=root(residual,guess,method='hybr',options=dict(xtol=min(1e-10,options.tolerance/10),maxfev=options.maximum_residual_evaluations))
        state=np.zeros(circuit.dimension);state[:circuit.dynamic_end]=answer.x*scale
        audit=assess_periodic(circuit,state,options)
        attempts.append(dict(seed_speed_pu=guess_speed,solver_success=bool(answer.success),maximum_normalized_residual=audit['maximum_normalized_rotating_residual']))
        if audit['qualified']:
            circuit._periodic_initial_state=state.copy()
            circuit.initialization=dict(method='synchronous_periodic',**audit,residual_evaluations=evaluations,attempts=attempts)
            return state
    raise ValueError('no admissible high-speed periodic operating point found; residuals: '+str(attempts))


def phase_ports(settings,initialization,nodes):
    """Export machine states only; caller must initialize its own phase network."""
    from power_grid_env.phase_network import MotorPort
    if not initialization.get('qualified') or set(nodes)!={m.id for m in settings.motors}:
        raise ValueError('qualified initialization and exact motor terminal mapping required')
    ports=[]
    for p in settings.motors:
        data=initialization['motors'][p.id];passport=p.model_copy(deep=True);passport.initial_speed_rad_s=data['speed_rad_s']
        ports.append(MotorPort(id=p.id,nodes=nodes[p.id],passport=passport,initial_currents_ab_a=data['currents_ab_a']))
    return ports
