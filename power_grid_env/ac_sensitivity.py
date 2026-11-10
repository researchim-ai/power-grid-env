"""Sparse local forward/adjoint sensitivities for audited distributed P/Q states.

Derivatives apply to one smooth active set. No derivative through switching,
certified trust region, global loading margin or dynamic stability is claimed.
"""
from pathlib import Path
from typing import Literal
import math
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from scipy.sparse.linalg import splu
from power_grid_env.ac_dispatch import DispatchOptions,DispatchSystem,solve_dispatch,audit_dispatch
from power_grid_env.balanced_ac import case_from_document
from power_grid_env.network_opf import _branch_quad
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class LoadChange(Strict):
    bus_id:int
    dp_mw:float=Field(default=0,ge=-1e9,le=1e9)
    dq_mvar:float=Field(default=0,ge=-1e9,le=1e9)


class Direction(Strict):
    name:str=Field(min_length=1,max_length=80)
    loads:list[LoadChange]=Field(min_length=1,max_length=2048)

    @model_validator(mode='after')
    def unique(self):
        if len({p.bus_id for p in self.loads})!=len(self.loads):raise ValueError('duplicate direction bus')
        return self


class Output(Strict):
    name:str=Field(min_length=1,max_length=80)
    kind:Literal['voltage','angle','pg','delta','branch_p','branch_q','branch_mva','branch_current']
    bus_id:int|None=None
    generator_index:int|None=Field(default=None,ge=0)
    branch_index:int|None=Field(default=None,ge=0)
    terminal:Literal['from','to']|None=None

    @model_validator(mode='after')
    def fields(self):
        expected={'voltage':{'bus_id'},'angle':{'bus_id'},'pg':{'generator_index'},'delta':set()}.get(self.kind,{'branch_index','terminal'})
        provided={k for k in ['bus_id','generator_index','branch_index','terminal'] if getattr(self,k) is not None}
        if provided!=expected:raise ValueError('output identity fields do not match kind')
        return self


class SensitivityInput(Strict):
    case:dict
    dispatch:DispatchOptions
    state:dict|None=None
    directions:list[Direction]=Field(default_factory=list,max_length=32)
    outputs:list[Output]=Field(min_length=1,max_length=32)
    switch_margin_pu:float=Field(default=1e-7,ge=1e-10,le=.01)
    top_n:int=Field(default=10,ge=1,le=64)
    provenance:dict=Field(default_factory=dict)

    @model_validator(mode='after')
    def names(self):
        for rows in [self.directions,self.outputs]:
            if len({p.name for p in rows})!=len(rows):raise ValueError('duplicate sensitivity name')
        return self


def active_set_guard(system,x,margin):
    vm,a,_=system.unpack(x);_,_,raw=system.generation(x);v=vm*np.exp(1j*a);z=(v*np.conj(system.y@v)).imag+system.qd-(vm-system.setpoint);distances=[];near=[]
    for i in np.flatnonzero((system.alpha>0)&(system.phi>system.plo)):
        distance=float(min(abs(raw[i]-system.plo[i]),abs(raw[i]-system.phi[i])));distances.append(distance)
        if distance<=margin:near.append(dict(kind='p',generator_index=int(i),distance_pu=distance))
    for i in np.flatnonzero(system.controlled&(system.hi>system.lo)):
        distance=float(min(abs(z[i]-system.lo[i]),abs(z[i]-system.hi[i])));distances.append(distance)
        if distance<=margin:near.append(dict(kind='q',bus_id=system.case.buses[i].id,distance_pu=distance))
    free=system.controlled&(system.hi>system.lo)&(z>=system.lo)&(z<=system.hi)
    return dict(smooth=not near,margin_pu=margin,minimum_distance_pu=min(distances,default=None),near_boundaries=near,certified_trust_region=False),free


def metric(system,x,output):
    n=system.n;vm,a,_=system.unpack(x);gradient=np.zeros(2*n);units='pu';v=vm*np.exp(1j*a)
    if output.kind in ('voltage','angle'):
        if output.bus_id not in system.idx:raise ValueError('unknown output bus')
        i=system.idx[output.bus_id]
        if output.kind=='voltage':gradient[n-1+i]=1.;value=vm[i]
        else:
            units='rad';value=a[i]
            if i!=system.ref:gradient[int(np.flatnonzero(system.angles==i)[0])]=1.
    elif output.kind in ('pg','delta'):
        units='MW'
        if output.kind=='delta':value=x[-1]*system.case.base_mva;gradient[-1]=system.case.base_mva
        else:
            i=output.generator_index
            if i>=len(system.case.generators):raise ValueError('unknown output generator')
            pg,_,raw=system.generation(x);value=pg[i]*system.case.base_mva
            if system.phi[i]>system.plo[i] and system.plo[i]<raw[i]<system.phi[i]:gradient[-1]=system.alpha[i]*system.case.base_mva
    else:
        if output.branch_index>=len(system.case.branches):raise ValueError('unknown output branch')
        line=system.case.branches[output.branch_index];units={'branch_p':'MW','branch_q':'Mvar','branch_mva':'MVA','branch_current':'pu-current'}[output.kind]
        if not line.status:return 0.,gradient,units
        f,t=system.idx[line.fbus],system.idx[line.tbus];ff,ft,tf,tt=_branch_quad(line);coeff=(ff,ft) if output.terminal=='from' else (tf,tt);bus=f if output.terminal=='from' else t
        current=coeff[0]*v[f]+coeff[1]*v[t];power=v[bus]*np.conj(current)*system.case.base_mva
        if output.kind=='branch_current' and abs(current)<=1e-12 or output.kind=='branch_mva' and abs(power)<=1e-12:raise ValueError('magnitude output is not differentiable at zero')
        value={'branch_p':power.real,'branch_q':power.imag,'branch_mva':abs(power),'branch_current':abs(current)}[output.kind]
        for j,c in [(f,coeff[0]),(t,coeff[1])]:
            for angular in [True,False]:
                dv=1j*v[j] if angular else np.exp(1j*a[j]);di=c*dv;ds=((dv if j==bus else 0j)*np.conj(current)+v[bus]*np.conj(di))*system.case.base_mva
                derivative={'branch_p':ds.real,'branch_q':ds.imag,'branch_mva':(np.conj(power)*ds).real/max(abs(power),1e-300),'branch_current':(np.conj(current)*di).real/max(abs(current),1e-300)}[output.kind]
                if angular:
                    if j!=system.ref:gradient[int(np.flatnonzero(system.angles==j)[0])]+=derivative
                else:gradient[n-1+j]+=derivative
    return float(value),gradient,units


def sensitivities(document):
    d=SensitivityInput.model_validate(document);case=case_from_document(d.case);system=DispatchSystem(case,d.dispatch);producer=package_fingerprint()
    # Reject unknown identities before solving, including failed-base requests.
    for direction in d.directions:
        if any(p.bus_id not in system.idx for p in direction.loads):raise ValueError('unknown direction bus')
    for output in d.outputs:
        if output.bus_id is not None and output.bus_id not in system.idx:raise ValueError('unknown output bus')
        if output.generator_index is not None and output.generator_index>=len(case.generators):raise ValueError('unknown output generator')
        if output.branch_index is not None and output.branch_index>=len(case.branches):raise ValueError('unknown output branch')
    if d.state is None:
        solved=solve_dispatch(case,d.dispatch)
        state=solved['result'] if solved['completed'] else None
    else:state=d.state
    common=dict(schema='power-grid-env.ac-sensitivity/v1',producer_sha256=producer,scope='local fixed-active-set balanced AC derivatives, loads in MW/Mvar, angles relative to declared gauge',derivatives_through_switching=False,certified_trust_region=False,stability_proven=False)
    if state is None:return dict(common,completed=False,reason='base_not_converged',state=None,audit=None,guard=None,forward=[],adjoint=[])
    audit=audit_dispatch(case,d.dispatch,state,branches_required=False);x=system.pack(state,state['delta_mw']/case.base_mva);guard,free_q=active_set_guard(system,x,d.switch_margin_pu)
    metrics=[metric(system,x,o) for o in d.outputs]
    if not guard['smooth']:return dict(common,completed=False,reason='active_set_boundary',state=state,audit=audit,guard=guard,forward=[],adjoint=[])
    _,j=system.evaluate(x,True)
    try:factor=splu(j)
    except RuntimeError:return dict(common,completed=False,reason='singular_jacobian',state=state,audit=audit,guard=guard,forward=[],adjoint=[])
    forward=[];adjoint=[];n=system.n
    for direction in d.directions:
        dp=np.zeros(n);dq=np.zeros(n)
        for p in direction.loads:dp[system.idx[p.bus_id]]=p.dp_mw;dq[system.idx[p.bus_id]]=p.dq_mvar
        b=np.r_[dp,np.where(free_q,0.,dq)]/case.base_mva;dx=factor.solve(-b);residual=float(max(abs(j@dx+b),default=0.)/max(1.,max(abs(b),default=0.)))
        if not np.isfinite(dx).all() or residual>1e-8:raise ValueError('forward sensitivity linear solve failed')
        forward.append(dict(name=direction.name,linear_residual=residual,angle_rad_per_step=[float(dx[int(np.flatnonzero(system.angles==i)[0])]) if i!=system.ref else 0. for i in range(n)],voltage_pu_per_step=dx[n-1:-1].tolist(),delta_mw_per_step=float(dx[-1]*case.base_mva),outputs={o.name:float(g@dx) for o,(_,g,_) in zip(d.outputs,metrics,strict=True)}))
    for output,(value,gradient,units) in zip(d.outputs,metrics,strict=True):
        dual=factor.solve(gradient,trans='T');residual=float(max(abs(j.T@dual-gradient),default=0.)/max(1.,max(abs(gradient),default=0.)))
        if not np.isfinite(dual).all() or residual>1e-8:raise ValueError('adjoint sensitivity linear solve failed')
        gp=-dual[:n]/case.base_mva;gq=-dual[n:]*~free_q/case.base_mva
        rows=[dict(bus_id=b.id,per_mw=float(gp[i]),per_mvar=float(gq[i])) for i,b in enumerate(case.buses)]
        adjoint.append(dict(name=output.name,kind=output.kind,value=value,output_units=units,linear_residual=residual,load_gradients=rows,ranked_p_buses=[r['bus_id'] for r in sorted(rows,key=lambda r:(-abs(r['per_mw']),r['bus_id']))[:d.top_n]],ranked_q_buses=[r['bus_id'] for r in sorted(rows,key=lambda r:(-abs(r['per_mvar']),r['bus_id']))[:d.top_n]]))
    if producer!=package_fingerprint():raise ValueError('sensitivity producer changed')
    return dict(common,completed=True,reason='local_sensitivities_computed',state=state,audit=audit,guard=guard,forward=forward,adjoint=adjoint)


def run_sensitivities(document,out):
    result=sensitivities(document);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    (out/'input.json').write_bytes(encoded(document));(out/'result.json').write_bytes(encoded(result));seal(out);return result


def verify_sensitivities(out):
    out=Path(out);identity=verify(out);expected=sensitivities(read_json(out/'input.json'))
    if encoded(read_json(out/'result.json'))!=encoded(expected):raise ValueError('sensitivity evidence differs')
    return dict(verified=True,completed=expected['completed'],reason=expected['reason'],manifest_sha256=identity,certified_trust_region=False)
