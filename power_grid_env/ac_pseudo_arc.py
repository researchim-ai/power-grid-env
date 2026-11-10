"""Sparse tangent predictor / hyperplane Newton corrector for balanced AC.

The metric is ||dx||²/n + d(lambda)² (angles radians, magnitudes pu).
A sign change of oriented tangent lambda is a sampled branch observation,
not proof of stability, a unique nose, or absence of other target solutions.
"""
from dataclasses import replace
import math,time
from pathlib import Path
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.ac_dispatch import DispatchOptions,solve_dispatch,audit_dispatch
from scipy.sparse import bmat,csc_matrix
from scipy.sparse.linalg import splu
from power_grid_env.balanced_ac import case_document,case_from_document,solve_power_flow,_allocate
from power_grid_env.balanced_sparse import admittance,derivatives
from power_grid_env.ac_continuation import compatible,physical_audit,ContinuationOptions,snapshot
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


class PseudoArcOptions(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    step:float=Field(default=.04,gt=0,le=.25)
    maximum_step:float=Field(default=.1,gt=0,le=.5)
    minimum_step:float=Field(default=1e-5,gt=0,le=.01)
    maximum_points:int=Field(default=96,ge=2,le=256)
    maximum_attempts:int=Field(default=192,ge=1,le=512)
    corrector_iterations:int=Field(default=14,ge=2,le=40)
    maximum_wall_s:float=Field(default=120,gt=0,le=600)
    tolerance_pu:float=Field(default=1e-10,gt=0,le=1e-7)
    minimum_lambda:float=Field(default=-.1,ge=-1,le=0)
    maximum_lambda:float=Field(default=1.25,ge=1,le=3)
    points_after_turn:int=Field(default=4,ge=1,le=32)
    turn_refinement_iterations:int=Field(default=12,ge=0,le=24)
    enforce_q_limits:bool=Field(default=False,description='Projected Q controls; slack Q also enforced when dispatch is configured')
    dispatch:DispatchOptions|None=None

    @model_validator(mode='after')
    def distributed_policy(self):
        if self.dispatch is not None and (not self.enforce_q_limits or self.dispatch.tolerance_pu!=self.tolerance_pu):
            raise ValueError('distributed continuation requires Q controls and identical dispatch/arc tolerance')
        return self


class PolarFamily:
    def __init__(self,start,target):
        compatible(start,target);self.start=start;self.target=target;self.n=len(start.buses)
        self.index={b.id:i for i,b in enumerate(start.buses)}
        self.nonref=np.array([i for i,b in enumerate(start.buses) if b.type_code!=3],int)
        self.pq=np.array([i for i,b in enumerate(start.buses) if b.type_code==1],int)
        self.size=len(self.nonref)+len(self.pq);self.scale=math.sqrt(max(1,self.size))
        self.y=admittance(start,self.index)
        self.dp=np.array([b.pd-a.pd for a,b in zip(start.buses,target.buses)])
        self.dq=np.array([b.qd-a.qd for a,b in zip(start.buses,target.buses)])
        self.pg=np.zeros(self.n);self.qg=np.zeros(self.n)
        for g in start.generators:self.pg[self.index[g.bus]]+=g.pg or 0.;self.qg[self.index[g.bus]]+=g.qg or 0.
        self.g=np.r_[self.dp[self.nonref],self.dq[self.pq]]/start.base_mva
        self.audit_options=ContinuationOptions(tolerance_pu=1e-10)

    def audit(self,result,lam):return physical_audit(self.load_case(lam),result,self.audit_options)

    def pack(self,r,lam):
        return np.r_[np.r_[np.deg2rad(r['va_deg'])[self.nonref],np.asarray(r['vm_pu'])[self.pq]]/self.scale,lam]

    def unpack(self,u):
        vm=np.array([b.vm for b in self.start.buses],float);angles=np.zeros(self.n)
        angles[self.nonref]=u[:len(self.nonref)]*self.scale
        vm[self.pq]=u[len(self.nonref):self.size]*self.scale
        return vm,angles,float(u[-1])

    def valid(self,u,o):
        vm,_,lam=self.unpack(u)
        return bool(np.isfinite(u).all() and np.all((vm>.02)&(vm<3.)) and o.minimum_lambda<=lam<=o.maximum_lambda)

    def load_case(self,lam):
        return replace(self.target,buses=[replace(a,pd=b.pd if lam==1 else a.pd+lam*d,qd=b.qd if lam==1 else a.qd+lam*e)
            for a,b,d,e in zip(self.start.buses,self.target.buses,self.dp,self.dq)])

    def evaluate(self,u,jacobian=False):
        vm,a,lam=self.unpack(u);v=vm*np.exp(1j*a);power=v*np.conj(self.y@v)
        pd=np.array([b.pd for b in self.start.buses])+lam*self.dp;qd=np.array([b.qd for b in self.start.buses])+lam*self.dq
        f=np.r_[power.real[self.nonref]-(self.pg[self.nonref]-pd[self.nonref])/self.start.base_mva,
                power.imag[self.pq]-(self.qg[self.pq]-qd[self.pq])/self.start.base_mva]
        if not jacobian:return f
        p,q,pv,qv=derivatives(vm,a,self.y)
        j=bmat([[p[self.nonref,:][:,self.nonref],pv[self.nonref,:][:,self.pq]],
                 [q[self.pq,:][:,self.nonref],qv[self.pq,:][:,self.pq]]],format='csc')*self.scale
        return f,j

    def bordered(self,j,t):return bmat([[j,csc_matrix(self.g[:,None])],[csc_matrix(t[None,:-1]),csc_matrix([[t[-1]]])]],format='csc')

    def tangent(self,u,previous):
        _,j=self.evaluate(u,True);rhs=np.zeros(self.size+1);rhs[-1]=1
        t=splu(self.bordered(j,previous)).solve(rhs);t/=np.linalg.norm(t)
        if t@previous<0:t=-t
        return t

    def result(self,u):
        vm,a,lam=self.unpack(u);case=self.load_case(lam);s=vm*np.exp(1j*a);power=s*np.conj(self.y@s)*case.base_mva
        pg=np.array([g.pg or 0. for g in case.generators]);qg=np.array([g.qg or 0. for g in case.generators])
        for bus,b in enumerate(case.buses):
            units=[k for k,g in enumerate(case.generators) if g.bus==b.id]
            if not units:continue
            if b.type_code==3:pg[units]=_allocate(power[bus].real+b.pd,[case.generators[k].pmin for k in units],[case.generators[k].pmax for k in units])
            if b.type_code in (2,3):qg[units]=_allocate(power[bus].imag+b.qd,[case.generators[k].qmin for k in units],[case.generators[k].qmax for k in units])
        return dict(completed=True,status='corrected_branch_point',bus_ids=[b.id for b in case.buses],vm_pu=vm.tolist(),va_deg=np.rad2deg(a).tolist(),pg_mw=pg.tolist(),qg_mvar=qg.tolist())


def correct(family,predicted,tangent,o):
    u=predicted.copy()
    for iteration in range(o.corrector_iterations+1):
        if not family.valid(u,o):return None,iteration,'state_domain'
        f,j=family.evaluate(u,True);h=np.r_[f,tangent@(u-predicted)]
        norm=float(max(abs(h),default=0.))
        if not math.isfinite(norm):return None,iteration,'nonfinite_corrector'
        if norm<=o.tolerance_pu:return u,iteration,None
        if iteration==o.corrector_iterations:break
        try:delta=splu(family.bordered(j,tangent)).solve(-h)
        except RuntimeError:return None,iteration,'singular_bordered_jacobian'
        improved=False
        for factor in (1.,.5,.25,.125,.0625,.03125):
            candidate=u+factor*delta
            if family.valid(candidate,o):
                residual=np.r_[family.evaluate(candidate),tangent@(candidate-predicted)]
                if max(abs(residual),default=0.)<norm:u=candidate;improved=True;break
        if not improved:return None,iteration,'corrector_line_search'
    return None,o.corrector_iterations,'corrector_iteration_budget'



def refine_turn(family,anchor,tangent,right_step,o,started,cancelled=None):
    left=0.;right=right_step;best=None;history=[]
    for _ in range(o.turn_refinement_iterations):
        if (cancelled is not None and cancelled()) or time.monotonic()-started>=o.maximum_wall_s:break
        step=(left+right)/2;predicted=anchor+step*tangent
        u,it,reason=correct(family,predicted,tangent,o)
        if u is None:break
        try:t=family.tangent(u,tangent)
        except RuntimeError:break
        f=float(max(abs(family.evaluate(u)),default=0.))
        history.append(dict(arc_step=step,tangent_lambda=float(t[-1]),maximum_residual_pu=f))
        best=dict(lambda_value=float(u[-1]),result=snapshot(family.result(u)),tangent=t.tolist(),
            arc_step=step,maximum_residual_pu=f,remaining_arc_width=right-left)
        if t[-1]>0:left=step
        else:right=step
    return dict(estimate=best,history=history,certified_lambda_interval=False)


def refine_control_change(family,anchor,tangent,right_step,bus,old_mode,o,started,control_kind='q',cancelled=None):
    """Bisect a detected control change in the predictor hyperplane coordinate.

    Only a bracket for this sampled transition is claimed; no guarantee that
    multiple intervening switches in a coarse accepted step were all observed.
    """
    left=0.;right=right_step;left_u=anchor.copy();right_u=None;history=[]
    for _ in range(20):
        if (cancelled is not None and cancelled()) or time.monotonic()-started>=o.maximum_wall_s:break
        step=(left+right)/2
        u,_,failure=correct(family,anchor+step*tangent,tangent,o)
        if u is None:break
        mode=(family.p_control(u) if control_kind=='p' else family.control(u)[2])[bus];history.append(dict(arc_step=step,mode=mode))
        if mode==old_mode:left=step;left_u=u
        else:right=step;right_u=u
        if right-left<=1e-7:break
    def state(u,step):
        return None if u is None else dict(arc_step=step,lambda_value=float(u[-1]),result=snapshot(family.result(u)))
    return dict(left=state(left_u,left),right=state(right_u,right),history=history,
        localized=right_u is not None and right-left<=1e-7,arc_tolerance=1e-7,
        scope='sampled mode-change bracket in predictor hyperplane coordinate; not exhaustive event detection')


def outcome(o,code,reason,points,attempts,turns,final,started,backend):
    controls=[];changes=[]
    if o.enforce_q_limits:
        # Recorded by the driver from the projected control equations.
        controls=[p['control_modes'] for p in points]
        for i in range(1,len(points)):
            for bus,mode in sorted(controls[i].items()):
                before=controls[i-1][bus]
                if mode!=before:changes.append(dict(bus_id=int(bus),before=before,after=mode,
                    before_point=i-1,after_point=i,refinement=points[i]['control_brackets'][bus]))
    p_changes=[]
    if o.dispatch is not None:
        for i in range(1,len(points)):
            for unit,mode in sorted(points[i]['p_control_modes'].items()):
                before=points[i-1]['p_control_modes'][unit]
                if mode!=before:p_changes.append(dict(generator_index=int(unit),before=before,after=mode,before_point=i-1,after_point=i,refinement=points[i]['p_control_brackets'][unit]))
    return dict(p_control_changes=p_changes,control_changes=changes,schema='power-grid-env.ac-pseudo-arc/v1',completed=final is not None,reason=reason,producer_sha256=code,options=o.model_dump(mode='json'),
        backend=backend,points=points,attempts=attempts,turns=turns,final_result=final,elapsed_s=time.monotonic()-started,
        target_absence_proven=False,stability_proven=False,voltage_collapse_proven=False,
        control_model=control_model(o),slack_q_enforced=o.dispatch is not None,
        metric=('angles radians, all Vm pu and delta pu divided by sqrt(2n); lambda unscaled' if o.dispatch is not None else 'angles radians and all nonreference Vm pu divided by sqrt(state count); lambda unscaled' if o.enforce_q_limits else 'angles radians and PQ Vm pu divided by sqrt(state count); lambda unscaled'),
        scope='sampled distributed P/Q control branch; separate phase reference; no frequency or global absence proof' if o.dispatch is not None else 'sampled projected-Q control branch (slack Q unrestricted)' if o.enforce_q_limits else 'sampled fixed-PQ/PV solution branch; no reactive-limit switching, stability or global solution-exclusion proof')


def control_model(o):return 'distributed-pq/v1' if o.dispatch is not None else 'projected-pv-q/v1' if o.enforce_q_limits else 'fixed-pq-pv/v1'


def make_family(start,target,options):
    if options.dispatch is not None:
        from power_grid_env.ac_dispatch_arc import DispatchFamily
        return DispatchFamily(start,target,options.dispatch)
    if options.enforce_q_limits:
        from power_grid_env.ac_reactive_arc import ReactivePolarFamily
        return ReactivePolarFamily(start,target)
    return PolarFamily(start,target)


def trace_power_flow(start,target,options=None,*,cancelled=None,_backend=None,initial_state=None):
    o=PseudoArcOptions.model_validate(options or {});o=PseudoArcOptions.model_validate(o.model_dump(mode='json'))
    if not o.minimum_step<=o.step<=o.maximum_step:raise ValueError('minimum <= initial <= maximum arc step required')
    start=case_from_document(case_document(start));target=case_from_document(case_document(target));family=make_family(start,target,o) if _backend is None else _backend.family(start,target)
    if o.enforce_q_limits and _backend is not None:raise ValueError('reference backend does not implement projected Q controls')
    backend='native' if _backend is None else _backend.name
    family.audit_options=ContinuationOptions(tolerance_pu=o.tolerance_pu,enforce_q_limits=o.enforce_q_limits)
    if initial_state is not None:
        if o.dispatch is None or _backend is not None:raise ValueError('audited initial state requires native distributed P/Q controls')
        audit_dispatch(start,o.dispatch,initial_state,branches_required=False)
        import copy
        initial_state=copy.deepcopy(initial_state)
    code=package_fingerprint();began=time.monotonic();points=[];attempts=[];turns=[];final=None;reason='maximum_attempts';after=None
    if cancelled is not None and cancelled():return outcome(o,code,'cancelled',[],[],[],None,began,backend)
    if initial_state is not None:
        base=initial_state
    elif o.dispatch is not None:
        seed_options=o.dispatch.model_copy(update={'maximum_wall_s':min(o.maximum_wall_s,o.dispatch.maximum_wall_s)})
        seed=solve_dispatch(start,seed_options.model_dump(mode='json'),cancelled=cancelled)
        if not seed['completed'] and seed['reason'] in {'cancelled','wall_deadline','producer_changed'}:return outcome(o,code,seed['reason'],[],[],[],None,began,backend)
        base=seed['result'] if seed['completed'] else dict(completed=False)
    else:
        base=solve_power_flow(start,linear_solver='sparse',enforce_q_limits=o.enforce_q_limits,tolerance_pu=o.tolerance_pu) if _backend is None else _backend.solve(start,0.)
    if initial_state is None and o.enforce_q_limits and base.get('completed'):
        seed=family.pack(base,0.);fixed=np.zeros(family.size+1);fixed[-1]=1.
        adjusted,_,_=correct(family,seed,fixed,o)
        if adjusted is not None:base=family.result(adjusted)
    audit=family.audit(base,0.)
    if o.enforce_q_limits and base.get('completed') and max(abs(family.evaluate(family.pack(base,0.))),default=0.)>o.tolerance_pu*2:audit=None
    if not audit or not audit['balance_acceptable']:
        return outcome(o,code,'base_not_converged',[],[],[],None,began,backend)
    u=family.pack(base,0.);previous=np.zeros(family.size+1);previous[-1]=1.
    try:t=family.tangent(u,previous)
    except RuntimeError:return outcome(o,code,'initial_tangent_failed',[],[],[],None,began,backend)
    if time.monotonic()-began>=o.maximum_wall_s:return outcome(o,code,'wall_deadline',[],[],[],None,began,backend)
    points.append(dict(lambda_value=0.,tangent=t.tolist(),result=snapshot(base),audit=audit));step=o.step
    if o.enforce_q_limits:points[-1]['control_modes']={str(k):v for k,v in family.control(u)[2].items()}
    if o.dispatch is not None:points[-1]['p_control_modes']={str(k):v for k,v in family.p_control(u).items()}
    for _ in range(o.maximum_attempts):
        if cancelled is not None and cancelled():reason='cancelled';break
        if time.monotonic()-began>=o.maximum_wall_s:reason='wall_deadline';break
        if package_fingerprint()!=code:reason='producer_changed';break
        predicted=u+step*t;candidate,iterations,failure=correct(family,predicted,t,o)
        r=None;point_audit=None;new_t=None
        if candidate is not None:
            r=family.result(candidate);point_audit=family.audit(r,float(candidate[-1]))
            if not point_audit['balance_acceptable']:failure='point_physics';candidate=None
            else:
                try:new_t=family.tangent(candidate,t)
                except RuntimeError:failure='tangent_failed';candidate=None
        if candidate is not None and o.enforce_q_limits:
            old_modes=family.control(u)[2];new_modes=family.control(candidate)[2]
            if any({old_modes[k],new_modes[k]}=={'lower','upper'} for k in old_modes):
                candidate=None;failure='multiple_control_transitions'
        if candidate is not None and o.dispatch is not None:
            old_modes=family.p_control(u);new_modes=family.p_control(candidate)
            if any({old_modes[k],new_modes[k]}=={'lower','upper'} for k in old_modes):candidate=None;failure='multiple_p_control_transitions'
        if cancelled is not None and cancelled():reason='cancelled';candidate=None;failure=reason
        if time.monotonic()-began>=o.maximum_wall_s:reason='wall_deadline';candidate=None;failure=reason
        if package_fingerprint()!=code:reason='producer_changed';candidate=None;failure=reason
        accepted=candidate is not None
        attempts.append(dict(anchor=len(points)-1,step=step,accepted=accepted,corrector_iterations=iterations,reason=failure))
        if failure in {'wall_deadline','producer_changed','cancelled'}:break
        if not accepted:
            step/=2
            if step<o.minimum_step:reason='minimum_step';break
            continue
        lam=float(candidate[-1]);arc_error=abs(float(t@(candidate-predicted)))
        points.append(dict(lambda_value=lam,tangent=new_t.tolist(),result=snapshot(r),audit=point_audit,arc_error=arc_error,
            predictor_anchor=len(points)-1,arc_step=step))
        if o.enforce_q_limits:
            modes={str(k):v for k,v in family.control(candidate)[2].items()}
            points[-1]['control_modes']=modes;points[-1]['control_brackets']={}
            for bus,mode in modes.items():
                old=points[-2]['control_modes'][bus]
                if old!=mode:
                    points[-1]['control_brackets'][bus]=refine_control_change(family,u,t,step,int(bus),old,o,began,cancelled=cancelled)
            # Refinement changes the family's current lambda column; the next
            # predictor recomputes all equations before using it.

        if o.dispatch is not None:
            modes={str(k):v for k,v in family.p_control(candidate).items()}
            points[-1]['p_control_modes']=modes;points[-1]['p_control_brackets']={}
            for unit,mode in modes.items():
                old=points[-2]['p_control_modes'][unit]
                if old!=mode:points[-1]['p_control_brackets'][unit]=refine_control_change(family,u,t,step,int(unit),old,o,began,control_kind='p',cancelled=cancelled)

        if t[-1]>0 and new_t[-1]<=0:
            turns.append(dict(before=len(points)-2,after=len(points)-1,refinement=refine_turn(family,u,t,step,o,began,cancelled=cancelled)));after=0
        elif after is not None:after+=1
        if cancelled is not None and cancelled():reason='cancelled';break
        if time.monotonic()-began>=o.maximum_wall_s:reason='wall_deadline';break
        if (u[-1]-1)*(lam-1)<=0 and max(u[-1],lam)>=1:
            fraction=(1-u[-1])/(lam-u[-1]) if lam!=u[-1] else 1.
            guess=u+fraction*(candidate-u);vm,a,_=family.unpack(guess)
            exact=replace(target,buses=[replace(b,vm=b.vm if b.type_code in (2,3) else float(vm[i]),va_deg=float(np.rad2deg(a[i]))) for i,b in enumerate(target.buses)])
            if o.enforce_q_limits:
                fixed=np.zeros(family.size+1);fixed[-1]=1.;guess[-1]=1.
                adjusted,_,_=correct(family,guess,fixed,o)
                solved=family.result(adjusted) if adjusted is not None else dict(completed=False)
            else:
                solved=solve_power_flow(exact,linear_solver='sparse',enforce_q_limits=False,tolerance_pu=o.tolerance_pu) if _backend is None else _backend.solve(exact,1.)
            if cancelled is not None and cancelled():reason='cancelled';break
            if time.monotonic()-began>=o.maximum_wall_s:reason='wall_deadline';break
            if package_fingerprint()!=code:reason='producer_changed';break
            final_audit=family.audit(solved,1.)
            if solved['completed'] and final_audit and final_audit['balance_acceptable'] and max(abs(family.evaluate(family.pack(solved,1.))),default=0.)<=o.tolerance_pu*2:final=solved;reason='target_reached';u=candidate;t=new_t;break
        u=candidate;t=new_t
        if o.dispatch is not None and not family.has_p_reserve(u):reason='participating_p_limits_active';break
        if after is not None and after>=o.points_after_turn:reason='sampled_turn_followed';break
        if len(points)>=o.maximum_points:reason='maximum_points';break
        step=min(o.maximum_step,step*(1.25 if iterations<=4 else .7 if iterations>=9 else 1.))
    return outcome(o,code,reason,points,attempts,turns,final,began,backend)


def run_pseudo_arc(document,out):
    if set(document)-{'start','target','options','provenance','initial_state'}:raise ValueError('unknown pseudo-arc input fields')
    start=case_from_document(document['start']);target=case_from_document(document['target']);compatible(start,target)
    if len(encoded(document))>16000000:raise ValueError('pseudo-arc input budget exceeded')
    if document.get('initial_state') is not None:
        o=PseudoArcOptions.model_validate(document.get('options',{}))
        if o.dispatch is None:raise ValueError('audited seed requires distributed controls')
        audit_dispatch(start,o.dispatch,document['initial_state'],branches_required=False)
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    (out/'input.json').write_bytes(encoded(document));r=trace_power_flow(start,target,document.get('options'),initial_state=document.get('initial_state'))
    raw=encoded(r)
    if len(raw)>64000000:raise ValueError('pseudo-arc result budget exceeded')
    (out/'result.json').write_bytes(raw);seal(out);return r


def audit_trace(document,r,*,allow_reference=False):
    start=case_from_document(document['start']);target=case_from_document(document['target']);o=PseudoArcOptions.model_validate(document.get('options',{}))
    if not o.minimum_step<=o.step<=o.maximum_step:raise ValueError('invalid arc-step bounds')
    family=make_family(start,target,o);family.audit_options=ContinuationOptions(tolerance_pu=o.tolerance_pu,enforce_q_limits=o.enforce_q_limits)
    if r.get('schema')!='power-grid-env.ac-pseudo-arc/v1' or r.get('slack_q_enforced')!=(o.dispatch is not None) or r.get('control_model')!=control_model(o):raise ValueError('unsupported branch control model')
    if r['producer_sha256']!=package_fingerprint() or r['options']!=o.model_dump(mode='json'):raise ValueError('current pseudo-arc producer/options required')
    if not allow_reference and r['backend']!='native':raise ValueError('native branch artifact required')
    if r['stability_proven'] or r['target_absence_proven'] or r['voltage_collapse_proven']:raise ValueError('unsupported branch proof claim')
    allowed={'base_not_converged','initial_tangent_failed','cancelled','wall_deadline','producer_changed','maximum_attempts','maximum_points','minimum_step','sampled_turn_followed','target_reached','participating_p_limits_active'}
    if r['reason'] not in allowed or (r['completed']!=(r['reason']=='target_reached')):raise ValueError('invalid branch termination claim')
    points=r['points']
    if len(points)>o.maximum_points or len(r['attempts'])>o.maximum_attempts:raise ValueError('branch inventory exceeds budgets')
    if document.get('initial_state') is not None:
        if o.dispatch is None or r['backend']!='native':raise ValueError('audited seed requires native distributed controls')
        audit_dispatch(start,o.dispatch,document['initial_state'],branches_required=False)
        if points and encoded(snapshot(document['initial_state']))!=encoded(points[0]['result']):raise ValueError('initial branch state differs from declared seed')
    vectors=[];tangents=[]
    def check_state(lam,result,tangent):
        u=family.pack(result,lam)
        if not family.valid(u,o):raise ValueError('branch state outside declared domain')
        audit=family.audit(result,lam)
        if not audit or not audit['balance_acceptable']:raise ValueError('branch physical balance failed')
        f,j=family.evaluate(u,True)
        if max(abs(f),default=0.)>o.tolerance_pu*2:raise ValueError('branch equation residual exceeded')
        t=np.asarray(tangent,float)
        if t.shape!=u.shape or not np.isfinite(t).all() or abs(np.linalg.norm(t)-1)>1e-8:raise ValueError('invalid unit tangent')
        residual=j@t[:-1]+family.g*t[-1]
        scale=np.max(abs(j)@abs(t[:-1])+abs(family.g*t[-1]),initial=1.)
        if max(abs(residual),default=0.)/max(1.,scale)>1e-8:raise ValueError('tangent does not satisfy branch equations')
        return u,t,audit
    for i,p in enumerate(points):
        u,t,audit=check_state(p['lambda_value'],p['result'],p['tangent'])
        if p['audit']!=audit:raise ValueError('stored branch audit differs')
        if i==0:
            if p['lambda_value']!=0 or t[-1]<=0:raise ValueError('invalid initial branch orientation')
        else:
            if p['predictor_anchor']!=i-1 or not 0<p['arc_step']<=o.maximum_step or t@tangents[-1]<=0:raise ValueError('branch orientation/anchor differs')
            predicted=vectors[-1]+p['arc_step']*tangents[-1];error=abs(float(tangents[-1]@(u-predicted)))
            if error>o.tolerance_pu*2 or abs(error-p['arc_error'])>max(1e-11,o.tolerance_pu*.1):raise ValueError('pseudo-arclength hyperplane differs')
        if o.enforce_q_limits and p.get('control_modes')!={str(k):v for k,v in family.control(u)[2].items()}:raise ValueError('reactive control mode differs')
        if o.dispatch is not None and p.get('p_control_modes')!={str(k):v for k,v in family.p_control(u).items()}:raise ValueError('active participation mode differs')
        vectors.append(u);tangents.append(t)
    expected_changes=[]
    if o.enforce_q_limits:
        for i in range(1,len(points)):
            changed={bus for bus,mode in points[i]['control_modes'].items() if mode!=points[i-1]['control_modes'][bus]}
            if set(points[i].get('control_brackets',{}))!=changed:raise ValueError('control bracket inventory differs')
            for bus,mode in sorted(points[i]['control_modes'].items()):
                before=points[i-1]['control_modes'][bus]
                if mode!=before:expected_changes.append(dict(bus_id=int(bus),before=before,after=mode,before_point=i-1,after_point=i,refinement=points[i]['control_brackets'][bus]))
    expected_p_changes=[]
    if o.dispatch is not None:
        for i in range(1,len(points)):
            changed={k for k,v in points[i]['p_control_modes'].items() if v!=points[i-1]['p_control_modes'][k]}
            if set(points[i].get('p_control_brackets',{}))!=changed:raise ValueError('P control bracket inventory differs')
            for unit,mode in sorted(points[i]['p_control_modes'].items()):
                before=points[i-1]['p_control_modes'][unit]
                if mode!=before:expected_p_changes.append(dict(generator_index=int(unit),before=before,after=mode,before_point=i-1,after_point=i,refinement=points[i]['p_control_brackets'][unit]))
    for event,kind in [(e,'q') for e in expected_changes]+[(e,'p') for e in expected_p_changes]:
        i=event['before_point'];bracket=event['refinement'];left=bracket['left'];right=bracket['right']
        if bracket['arc_tolerance']!=1e-7 or len(bracket['history'])>20:raise ValueError('invalid control refinement budget')
        if left is None:raise ValueError('missing control bracket anchor')
        lo=0.;hi=points[event['after_point']]['arc_step'];seen_right=False
        for item in bracket['history']:
            mid=(lo+hi)/2
            if item['arc_step']!=mid or item['mode'] not in {event['before'],event['after']}:raise ValueError('control refinement journal differs')
            if item['mode']==event['before']:lo=mid
            else:hi=mid;seen_right=True
        if left['arc_step']!=lo or (right is not None and right['arc_step']!=hi) or (right is not None)!=seen_right:raise ValueError('control refinement endpoints differ')
        last=-1.
        for endpoint,expected_mode in [(left,event['before']),(right,event['after'])]:
            if endpoint is None:continue
            step=endpoint['arc_step'];u=family.pack(endpoint['result'],endpoint['lambda_value'])
            if not last<step<=points[event['after_point']]['arc_step']:raise ValueError('unordered control bracket')
            last=step
            if not family.valid(u,o) or max(abs(family.evaluate(u)),default=0.)>o.tolerance_pu*2:raise ValueError('control bracket electrical residual')
            if not family.audit(endpoint['result'],float(u[-1]))['balance_acceptable']:raise ValueError('control bracket physical balance')
            if abs(tangents[i]@(u-vectors[i]-step*tangents[i]))>o.tolerance_pu*2:raise ValueError('control bracket hyperplane differs')
            actual=(family.p_control(u)[event['generator_index']] if kind=='p' else family.control(u)[2][event['bus_id']])
            if actual!=expected_mode:raise ValueError('control bracket mode differs')
        localized=right is not None and right['arc_step']-left['arc_step']<=1e-7
        if bracket['localized']!=localized:raise ValueError('control localization claim differs')
    if r.get('p_control_changes',[])!=expected_p_changes:raise ValueError('P control change inventory differs')
    if r.get('control_changes',[])!=expected_changes:raise ValueError('control change inventory differs')
    anchor=0
    for a in r['attempts']:
        if a['anchor']!=anchor or not 0<a['step']<=o.maximum_step:raise ValueError('rollback journal anchor differs')
        if a['accepted']:
            anchor+=1
            if anchor>=len(points) or a['step']!=points[anchor]['arc_step']:raise ValueError('accepted branch inventory differs')
    if points and anchor!=len(points)-1:raise ValueError('missing accepted branch steps')
    if r['reason']=='participating_p_limits_active' and (o.dispatch is None or not vectors or family.has_p_reserve(vectors[-1])):raise ValueError('participation exhaustion differs')
    if r['reason']=='maximum_attempts' and len(r['attempts'])!=o.maximum_attempts:raise ValueError('attempt exhaustion differs')
    if r['reason']=='maximum_points' and len(points)!=o.maximum_points:raise ValueError('point exhaustion differs')
    if r['reason']=='minimum_step' and (not r['attempts'] or r['attempts'][-1]['accepted'] or r['attempts'][-1]['step']/2>=o.minimum_step):raise ValueError('minimum step stop differs')
    if r['reason']=='sampled_turn_followed' and (not r['turns'] or len(points)-1-r['turns'][-1]['after']<o.points_after_turn):raise ValueError('post-turn trace differs')
    expected=[(i-1,i) for i in range(1,len(points)) if tangents[i-1][-1]>0 and tangents[i][-1]<=0]
    if [(t['before'],t['after']) for t in r['turns']]!=expected:raise ValueError('sampled turn inventory differs')
    for turn in r['turns']:
        refinement=turn['refinement'];estimate=refinement['estimate']
        if refinement['certified_lambda_interval'] or len(refinement['history'])>o.turn_refinement_iterations:raise ValueError('unsupported turn certificate')
        if estimate is not None:
            u,t,_=check_state(estimate['lambda_value'],estimate['result'],estimate['tangent'])
            i=turn['before'];step=estimate['arc_step']
            if not 0<step<points[turn['after']]['arc_step'] or abs(tangents[i]@(u-vectors[i]-step*tangents[i]))>o.tolerance_pu*2:raise ValueError('turn refinement hyperplane differs')
    if r['completed']:
        if r['reason']!='target_reached' or r['final_result'] is None:raise ValueError('missing target operating point')
        final=family.audit(r['final_result'],1.)
        crossed=any((vectors[i-1][-1]-1)*(vectors[i][-1]-1)<=0 and max(vectors[i-1][-1],vectors[i][-1])>=1 for i in range(1,len(vectors)))
        if not crossed or not final or not final['balance_acceptable'] or max(abs(family.evaluate(family.pack(r['final_result'],1.))),default=0.)>o.tolerance_pu*2:raise ValueError('exact target is not verified')
    elif r['final_result'] is not None:raise ValueError('incomplete branch cannot claim target')
    return dict(verified=True,completed=r['completed'],accepted_points=len(points),sampled_turns=len(r['turns']),
        stability_proven=False,target_absence_proven=False,scope='recomputed electrical states, tangents, predictor hyperplanes, rollback anchors and target; no Newton/time replay or source authentication')


def verify_pseudo_arc(directory):
    directory=Path(directory);identity=verify(directory)
    result=audit_trace(read_json(directory/'input.json'),read_json(directory/'result.json'))
    return dict(manifest_sha256=identity,**result)
