"""Bounded static distributed balancing with a separate phase-angle reference.

Pg_i = clip(Pschedule_i + alpha_i * delta, Pmin_i, Pmax_i).
Every voltage-controlling generator bus, including the former slack, uses
projected Q/voltage control with release. The phase reference fixes only an
angle. This is neither frequency dynamics, AGC timing, nor economic dispatch.
"""
from pathlib import Path
import time,math
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from scipy.sparse import bmat,csc_matrix,diags
from scipy.sparse.linalg import splu
from power_grid_env.balanced_ac import case_document,case_from_document,solve_power_flow,components,_allocate,branch_results
from power_grid_env.balanced_sparse import admittance,derivatives
from power_grid_env.network_opf import audit_feasibility
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


class Participant(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    generator_index:int=Field(ge=0)
    weight:float=Field(gt=0,le=1e6)


class DispatchOptions(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    participants:list[Participant]=Field(min_length=1,max_length=4096)
    angle_reference_bus:int|None=None
    tolerance_pu:float=Field(default=1e-10,gt=0,le=1e-7)
    maximum_iterations:int=Field(default=60,ge=1,le=200)
    maximum_wall_s:float=Field(default=60,gt=0,le=600)

    @model_validator(mode='after')
    def unique(self):
        ids=[p.generator_index for p in self.participants]
        if len(set(ids))!=len(ids):raise ValueError('duplicate dispatch participant')
        return self


class DispatchSystem:
    def __init__(self,case,options):
        self.case=case;self.o=options;self.n=len(case.buses);self.idx={b.id:i for i,b in enumerate(case.buses)}
        if any(g.pg is None for g in case.generators):raise ValueError('explicit Pg schedules required, including the former slack')
        if any(not g.pmin<=g.pg<=g.pmax for g in case.generators):raise ValueError('Pg schedule outside generator P limits')
        if any(p.generator_index>=len(case.generators) for p in options.participants):raise ValueError('unknown dispatch participant')
        reference=next(b.id for b in case.buses if b.type_code==3) if options.angle_reference_bus is None else options.angle_reference_bus
        if reference not in self.idx:raise ValueError('unknown phase-angle reference bus')
        self.reference=reference;self.ref=self.idx[reference];self.angles=np.array([i for i in range(self.n) if i!=self.ref],int)
        self.alpha=np.zeros(len(case.generators));total=sum(p.weight for p in options.participants)
        for p in options.participants:self.alpha[p.generator_index]=p.weight/total
        if not any(self.alpha[i]>0 and g.pmin<g.pmax for i,g in enumerate(case.generators)):raise ValueError('at least one participant needs a nonzero P range')
        self.pg0=np.array([g.pg for g in case.generators])/case.base_mva
        self.plo=np.array([g.pmin for g in case.generators])/case.base_mva;self.phi=np.array([g.pmax for g in case.generators])/case.base_mva
        self.genbus=np.array([self.idx[g.bus] for g in case.generators],int)
        self.units_by_bus=[[] for _ in range(self.n)]
        for i,bus in enumerate(self.genbus):self.units_by_bus[bus].append(i)
        self.qfixed=np.zeros(self.n);self.lo=np.zeros(self.n);self.hi=np.zeros(self.n)
        self.controlled=np.array([b.type_code in (2,3) for b in case.buses]);self.setpoint=np.array([b.vm for b in case.buses])
        for i,g in enumerate(case.generators):
            bus=self.genbus[i];self.qfixed[bus]+=(g.qg or 0.)/case.base_mva;self.lo[bus]+=g.qmin/case.base_mva;self.hi[bus]+=g.qmax/case.base_mva
            if not self.controlled[bus] and not g.qmin<=(g.qg or 0.)<=g.qmax:raise ValueError('PQ generator Q schedule outside limits')
        self.y=admittance(case,self.idx);self.pd=np.array([b.pd for b in case.buses])/case.base_mva;self.qd=np.array([b.qd for b in case.buses])/case.base_mva
        if not all(np.isfinite(x).all() for x in [self.pg0,self.plo,self.phi,self.lo,self.hi,self.qfixed,self.pd,self.qd,self.y.data]):raise ValueError('nonfinite normalized dispatch parameters')

    def pack(self,result,delta=0.):
        a=np.deg2rad(result['va_deg']);return np.r_[a[self.angles]-a[self.ref],result['vm_pu'],delta]

    def unpack(self,x):
        a=np.zeros(self.n);a[self.angles]=x[:self.n-1]
        return x[self.n-1:2*self.n-1],a,x[-1]

    def valid(self,x):
        vm,_,_=self.unpack(x)
        return x.shape==(2*self.n,) and np.isfinite(x).all() and np.all((vm>.02)&(vm<3.))

    def generation(self,x):
        raw=self.pg0+self.alpha*x[-1];pg=np.clip(raw,self.plo,self.phi)
        # At exact endpoints choose the unsaturated generalized derivative.
        free=(raw>=self.plo)&(raw<=self.phi)&(self.phi>self.plo)
        slope=np.bincount(self.genbus,weights=self.alpha*free,minlength=self.n)
        return pg,slope,raw

    def evaluate(self,x,jacobian=False):
        vm,a,_=self.unpack(x);v=vm*np.exp(1j*a);s=v*np.conj(self.y@v);pg,slope,raw=self.generation(x)
        planned=np.bincount(self.genbus,weights=pg,minlength=self.n)
        q=s.imag+self.qd;z=q-(vm-self.setpoint)
        free=self.controlled&(self.hi>self.lo)&(z>=self.lo)&(z<=self.hi)
        fq=np.where(self.controlled,q-np.clip(z,self.lo,self.hi),q-self.qfixed)
        f=np.r_[s.real+self.pd-planned,fq]
        if not jacobian:return f
        pa,qa,pv,qv=derivatives(vm,a,self.y);keep=diags((~free).astype(float));voltage=diags(free.astype(float))
        j=bmat([[pa[:,self.angles],pv,csc_matrix(-slope[:,None])],
                [keep@qa[:,self.angles],keep@qv+voltage,csc_matrix((self.n,1))]],format='csc')
        return f,j

    def result(self,x,*,include_branches=True):
        vm,a,delta=self.unpack(x);pg,_,raw=self.generation(x);v=vm*np.exp(1j*a);q=(v*np.conj(self.y@v)).imag*self.case.base_mva+np.array([b.qd for b in self.case.buses])
        qg=np.array([g.qg or 0. for g in self.case.generators]);modes={}
        for bus,b in enumerate(self.case.buses):
            units=self.units_by_bus[bus]
            if self.controlled[bus]:
                qg[units]=_allocate(q[bus],[self.case.generators[i].qmin for i in units],[self.case.generators[i].qmax for i in units])
                z=q[bus]/self.case.base_mva-(vm[bus]-b.vm)
                modes[str(b.id)]='fixed' if self.lo[bus]==self.hi[bus] else 'lower' if z<self.lo[bus] else 'upper' if z>self.hi[bus] else 'voltage'
        p_modes=['fixed' if lo==hi else 'lower' if r<lo else 'upper' if r>hi else 'participating' if w>0 else 'scheduled' for r,lo,hi,w in zip(raw,self.plo,self.phi,self.alpha)]
        result=dict(completed=True,status='distributed_balance_converged',bus_ids=[b.id for b in self.case.buses],vm_pu=vm.tolist(),va_deg=np.rad2deg(a).tolist(),
            pg_mw=(pg*self.case.base_mva).tolist(),qg_mvar=qg.tolist(),delta_mw=float(delta*self.case.base_mva),
            participation=self.alpha.tolist(),p_modes=p_modes,q_modes=modes,angle_reference_bus=self.reference,
            **(dict(branches=branch_results(self.case,vm,a)) if include_branches else {}))
        return result


def audit_dispatch(case,options,result,*,branches_required=True):
    system=DispatchSystem(case,options)
    if result.get('completed') is not True:raise ValueError('completed dispatch state required')
    if result['bus_ids']!=[b.id for b in case.buses] or result['angle_reference_bus']!=system.reference:raise ValueError('dispatch bus/reference identity differs')
    x=system.pack(result,result['delta_mw']/case.base_mva)
    if not system.valid(x) or abs(result['va_deg'][system.ref])>1e-9:raise ValueError('invalid dispatch state or angle gauge')
    if max(abs(system.evaluate(x)),default=0.)>options.tolerance_pu*2:raise ValueError('distributed balancing equations not satisfied')
    expected=system.result(x,include_branches=False)
    for key in ['pg_mw','qg_mvar','participation']:
        a=np.asarray(result[key],float);b=np.asarray(expected[key],float)
        if a.shape!=b.shape or not np.isfinite(a).all() or np.max(abs(a-b),initial=0.)>1e-9:raise ValueError('dispatch allocation differs')
    if result['p_modes']!=expected['p_modes'] or result['q_modes']!=expected['q_modes']:raise ValueError('dispatch equipment modes differ')
    if branches_required and 'branches' not in result:raise ValueError('dispatch branch inventory required')
    if 'branches' in result:
        expected_branches=branch_results(case,np.array(result['vm_pu']),np.deg2rad(result['va_deg']))
        if len(result['branches'])!=len(expected_branches):raise ValueError('dispatch branch inventory differs')
        for row,other in zip(result['branches'],expected_branches,strict=True):
            if row.keys()!=other.keys():raise ValueError('dispatch branch fields differ')
            for key,value in other.items():
                if type(value) in (int,bool):
                    if row[key]!=value:raise ValueError('dispatch branch identity/flags differ')
                elif not math.isfinite(row[key]) or abs(row[key]-value)>1e-8:raise ValueError('dispatch branch measurements differ')
    audit=audit_feasibility(case,np.array(result['vm_pu']),np.deg2rad(result['va_deg']),np.array(result['pg_mw']),np.array(result['qg_mvar']),tolerance=max(options.tolerance_pu*10,1e-8),matrix_format='sparse')
    if audit['max_mismatch_pu']>max(options.tolerance_pu*10,1e-8):raise ValueError('dispatch nodal balance failed')
    return dict(balance_acceptable=True,maximum_mismatch_pu=audit['max_mismatch_pu'],within_all_reported_limits=audit['feasible'],
        remaining_up_mw=float(sum(g.pmax-p for g,p,w in zip(case.generators,result['pg_mw'],system.alpha) if w>0)),
        remaining_down_mw=float(sum(p-g.pmin for g,p,w in zip(case.generators,result['pg_mw'],system.alpha) if w>0)))


def solve_dispatch(case,options,*,cancelled=None):
    case=case_from_document(case_document(case));o=DispatchOptions.model_validate(options);system=DispatchSystem(case,o);code=package_fingerprint();began=time.monotonic();history=[];state=None;reason='iteration_budget';iterations=0
    def stop():
        if cancelled is not None and cancelled():return 'cancelled'
        if time.monotonic()-began>=o.maximum_wall_s:return 'wall_deadline'
        if package_fingerprint()!=code:return 'producer_changed'
        return None
    reason=stop()
    if reason is None and len(components(case))!=1:reason='islanded'
    if reason is None:
        # An unconstrained classical PF is only a starting guess, never a result.
        seed=solve_power_flow(case,enforce_q_limits=False,linear_solver='sparse',tolerance_pu=o.tolerance_pu)
        if not seed.get('completed'):seed=dict(vm_pu=[b.vm for b in case.buses],va_deg=[b.va_deg for b in case.buses],pg_mw=[g.pg for g in case.generators])
        delta=(sum(seed['pg_mw'])-sum(g.pg for g in case.generators))/case.base_mva
        x=system.pack(seed,delta)
        for iteration in range(o.maximum_iterations+1):
            reason=stop()
            if reason is not None:break
            if not system.valid(x):reason='state_domain';break
            f,j=system.evaluate(x,True);norm=float(max(abs(f),default=0.));iterations=iteration
            if norm<=o.tolerance_pu:
                state=system.result(x);reason='converged';break
            if iteration==o.maximum_iterations:reason='iteration_budget';break
            if not np.any(system.generation(x)[1]):reason='participating_p_limits_active';break
            try:dx=splu(j).solve(-f)
            except RuntimeError:reason='singular_dispatch_jacobian';break
            improved=False
            for factor in (1.,.5,.25,.125,.0625,.03125,.015625):
                trial=x+factor*dx
                if system.valid(trial):
                    residual=float(max(abs(system.evaluate(trial)),default=0.))
                    if math.isfinite(residual) and residual<norm:
                        history.append(dict(iteration=iteration,before_residual_pu=norm,after_residual_pu=residual,damping=factor));x=trial;improved=True;break
            if not improved:reason='dispatch_line_search_failed';break
    final_stop=stop()
    if final_stop is not None:reason=final_stop;state=None
    audit=None if state is None else audit_dispatch(case,o,state)
    return dict(schema='power-grid-env.ac-dispatch/v1',producer_sha256=code,options=o.model_dump(mode='json'),completed=state is not None,reason=reason,
        result=state,audit=audit,iterations=iterations,history=history,elapsed_s=time.monotonic()-began,
        policy='clipped incremental Pg participation; projected Q/voltage control including former slack; independent angle gauge',
        frequency_dynamics=False,economic_dispatch=False,automatic_islanding=False,target_absence_proven=False,stability_proven=False)


def run_dispatch(document,out,*,cancelled=None):
    if set(document)-{'case','options','provenance'}:raise ValueError('unknown distributed dispatch input field')
    case=case_from_document(document['case']);o=DispatchOptions.model_validate(document['options']);DispatchSystem(case,o)
    if len(encoded(document))>16000000:raise ValueError('dispatch input budget exceeded')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);(out/'input.json').write_bytes(encoded(document));r=solve_dispatch(case,o.model_dump(mode='json'),cancelled=cancelled)
    (out/'result.json').write_bytes(encoded(r));seal(out);return r


def verify_dispatch(directory):
    directory=Path(directory);identity=verify(directory);d=read_json(directory/'input.json');r=read_json(directory/'result.json');case=case_from_document(d['case']);o=DispatchOptions.model_validate(d['options'])
    if r['schema']!='power-grid-env.ac-dispatch/v1' or r['producer_sha256']!=package_fingerprint() or r['options']!=o.model_dump(mode='json'):raise ValueError('current distributed dispatch producer/options required')
    if any(r[k] for k in ['frequency_dynamics','economic_dispatch','automatic_islanding','target_absence_proven','stability_proven']):raise ValueError('unsupported dispatch scope claim')
    allowed={'converged','cancelled','wall_deadline','producer_changed','islanded','state_domain','iteration_budget','participating_p_limits_active','singular_dispatch_jacobian','dispatch_line_search_failed'}
    if r['reason'] not in allowed or r['completed']!=(r['reason']=='converged'):raise ValueError('dispatch termination claim differs')
    if not 0<=r['iterations']<=o.maximum_iterations or len(r['history'])>o.maximum_iterations:raise ValueError('dispatch iteration budget exceeded')
    for i,h in enumerate(r['history']):
        if h['iteration']!=i or h['damping'] not in (1.,.5,.25,.125,.0625,.03125,.015625) or not 0<=h['after_residual_pu']<h['before_residual_pu'] or not math.isfinite(h['before_residual_pu']):raise ValueError('dispatch Newton journal differs')
    if r['reason']=='iteration_budget' and r['iterations']!=o.maximum_iterations:raise ValueError('dispatch iteration exhaustion differs')
    if r['completed']:
        if r['audit']!=audit_dispatch(case,o,r['result']):raise ValueError('dispatch audit differs')
    elif r['result'] is not None or r['audit'] is not None:raise ValueError('incomplete dispatch cannot claim a state')
    return dict(verified=True,completed=r['completed'],reason=r['reason'],manifest_sha256=identity,scope='recomputed constrained allocation, Q control and nodal balance; no Newton/time replay, frequency or global absence proof')
