# SPDX-License-Identifier: MPL-2.0
# Equations adapted from Dynawo Oel2c and its blocks, copyright (c) 2021-2024 RTE.
# This file is subject to MPL-2.0; see assets/licenses/Dynawo-MPL-2.0.txt.
"""Eight-state OEL2C with explicit clocks and a nonpositive SEXS summation port.

Pinned Dynawo cap-guard-v1 and explicitly named equilibrium-guard-v2 use MSL 3.2.3.
Startup, strict equilibria and selected stationary memory have separate policies.
"""
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator

CHANNELS=('measured_current_pu','timer_s','reference_current_pu','filtered_reference_pu','pid_integral_pu','pid_derivative_memory_pu',
          'leadlag2_memory_pu','leadlag1_memory_pu','actual_current_pu','timer_error_s','inverse_time_pu','timer_target_pu',
          'reference_rate_pu_s','bias_current_pu','regulator_error_pu','pid_raw_pu','pid_output_pu','leadlag2_output_pu','output_pu',
          'enable_timer_active','enable_timer_expired','reset_timer_active','reset_timer_expired','enable_timer_start_s','reset_timer_start_s')


class OEL2CPassport(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    generator_id: str=Field(min_length=1)
    model: Literal['OEL2C_Dynawo_cap_guard_v1_SEXS_summation','OEL2C_Dynawo_equilibrium_guard_v2_SEXS_summation']='OEL2C_Dynawo_cap_guard_v1_SEXS_summation'
    coincidence_policy: Literal['legacy','independent_below_clock_batch_v1']='legacy'
    initialization: Literal['dynawo_startup','inactive_equilibrium','active_limited_equilibrium','current_regulating_equilibrium','selected_equilibrium']='dynawo_startup'
    avr_reference_initialization: Literal['unmodified','derive_from_operating_point_with_OEL','specified_for_current_regulation']='unmodified'
    equilibrium_boundary_policy: Literal['native_relations','reject_sensitive_neighborhood']='native_relations'
    equilibrium_probe_radius_pu: float | None=Field(default=None,ge=1e-12,le=1e-4)
    equilibrium_reference_current_pu: float | None=Field(default=None,ge=0)
    equilibrium_pid_output_pu: float | None=None
    equilibrium_stability_policy: Literal['default_checks','stationarity_only']='default_checks'
    specified_avr_reference_pu: float | None=Field(default=None,gt=0)
    regulating_voltage_bounds_pu: tuple[float,float] | None=None
    current_comparison_tolerance_pu: float=Field(default=1e-8,ge=1e-10,le=1e-6)
    rated_field_reaction_pu: float=Field(gt=0)
    cap_tolerance_pu: float=Field(default=1e-8,gt=0,le=1e-6)
    C1: float=Field(default=2.,gt=0,le=8)
    C2: float=Field(default=2.,gt=0,le=8)
    FixedRd: float=Field(default=.1,ge=0)
    FixedRu: float=Field(default=-.1,le=0)
    IInstPu: float=Field(default=1.6,gt=0)
    ILimPu: float=Field(default=.6,gt=0)
    IResetPu: float=Field(default=.05,ge=0)
    ITfPu: float=Field(default=1.,gt=0)
    IThOffPu: float=Field(default=.05,ge=0)
    K1: float=Field(default=-.5,le=0)
    K2: float=Field(default=1.,gt=0)
    KAct: float=Field(default=1.,gt=0)
    KdOel: float=Field(default=.01,ge=0)
    KFb: float=Field(default=1.,gt=0)
    KiOel: float=Field(default=2.,gt=0)
    KpOel: float=Field(default=.3,ge=0)
    Krd: float=Field(default=-.6,le=0)
    Kru: float=Field(default=.3,ge=0)
    KScale: float=Field(default=1.,gt=0)
    Kzru: float=Field(default=.5,gt=0,le=1)
    Sw1: bool=False
    tAOel: float=Field(default=.03,gt=0)
    tB1Oel: float=Field(default=.04,gt=0)
    tB2Oel: float=Field(default=.05,gt=0)
    tC1Oel: float=Field(default=.02,gt=0)
    tC2Oel: float=Field(default=.03,gt=0)
    tDOel: float=Field(default=.03,gt=0)
    tEn: float=Field(default=.1,ge=0,le=60)
    tFcl: float=Field(default=.2,gt=0)
    tMax: float=2.
    tMin: float=-2.
    tOff: float=Field(default=.1,ge=0,le=60)
    tROel: float=Field(default=.02,gt=0)
    VInvMaxPu: float=3.
    VInvMinPu: float=-3.
    VOel1MaxPu: float=Field(default=0.,le=0)
    VOel1MinPu: float=-.2
    VOel2MaxPu: float=Field(default=0.,le=0)
    VOel2MinPu: float=-.3
    VOel3MaxPu: float=Field(default=0.,le=0)
    VOel3MinPu: float=-.4

    @model_validator(mode='after')
    def physical(self):
        policy={'selected_equilibrium':'derive_from_operating_point_with_OEL','active_limited_equilibrium':'derive_from_operating_point_with_OEL','current_regulating_equilibrium':'specified_for_current_regulation'}.get(self.initialization,'unmodified')
        if self.avr_reference_initialization!=policy:raise ValueError('explicit compatible AVR reference policy required for selected OEL2C initialization')
        if self.equilibrium_boundary_policy=='reject_sensitive_neighborhood':
            if self.initialization!='selected_equilibrium' or self.equilibrium_probe_radius_pu is None:raise ValueError('boundary checking requires selected_equilibrium and an explicit probe radius')
        elif self.equilibrium_probe_radius_pu is not None:raise ValueError('probe radius requires reject_sensitive_neighborhood policy')
        if self.initialization=='selected_equilibrium':
            if self.model!='OEL2C_Dynawo_equilibrium_guard_v2_SEXS_summation' or self.equilibrium_stability_policy!='stationarity_only':raise ValueError('selected equilibrium requires v2 and explicit stationarity_only policy')
        elif self.equilibrium_reference_current_pu is not None or self.equilibrium_pid_output_pu is not None or self.equilibrium_stability_policy!='default_checks':raise ValueError('stationary memory inputs require selected_equilibrium')
        if self.initialization=='current_regulating_equilibrium':
            if self.model!='OEL2C_Dynawo_equilibrium_guard_v2_SEXS_summation':raise ValueError('current regulation requires explicitly named equilibrium_guard_v2')
            if self.specified_avr_reference_pu is None or self.regulating_voltage_bounds_pu is None:raise ValueError('specified AVR reference and voltage bounds required for current regulation')
            lo,hi=self.regulating_voltage_bounds_pu
            if not 0<lo<hi:raise ValueError('ordered positive regulating voltage bounds required')
        elif self.specified_avr_reference_pu is not None or self.regulating_voltage_bounds_pu is not None:raise ValueError('regulating inputs require current_regulating_equilibrium')
        if self.model=='OEL2C_Dynawo_cap_guard_v1_SEXS_summation' and self.current_comparison_tolerance_pu!=1e-8:raise ValueError('current comparison band is only configurable in equilibrium_guard_v2')
        if not self.ILimPu<self.IInstPu:raise ValueError('thermal current must be below instantaneous current')
        if self.cap_tolerance_pu>=1e-4*(self.IInstPu-self.ILimPu):raise ValueError('cap tolerance must be small relative to current-limit interval')
        if self.model=='OEL2C_Dynawo_equilibrium_guard_v2_SEXS_summation' and not 128*np.finfo(float).eps*max(1.,self.ITfPu,self.IInstPu)<self.current_comparison_tolerance_pu<1e-4*(self.IInstPu-self.ILimPu):raise ValueError('v2 comparison band must be representable and small relative to current limits')
        if not self.tMin<self.tFcl<self.tMax:raise ValueError('timer reference must lie inside bounds')
        if not self.VInvMinPu<self.VInvMaxPu:raise ValueError('ordered inverse-time limits required')
        for n in (1,2,3):
            if not getattr(self,f'VOel{n}MinPu')<getattr(self,f'VOel{n}MaxPu')==0.:raise ValueError('SEXS summation requires negative minimum and zero maximum at each output stage')
        return self

    def native_parameters(self):
        return self.model_dump(exclude={'coincidence_policy','generator_id','model','rated_field_reaction_pu','initialization','cap_tolerance_pu','avr_reference_initialization','specified_avr_reference_pu','regulating_voltage_bounds_pu','current_comparison_tolerance_pu','equilibrium_reference_current_pu','equilibrium_pid_output_pu','equilibrium_stability_policy','equilibrium_boundary_policy','equilibrium_probe_radius_pu'})


class OEL2CDynamics:
    """Pure RHS between explicit state/clock events; no trial-call mutations."""
    def __init__(self,p):self.p=p;self.context={}

    def criteria(self,x):
        p=self.p;band=p.current_comparison_tolerance_pu if p.model=='OEL2C_Dynawo_equilibrium_guard_v2_SEXS_summation' else 0.
        return dict(scaled_low=p.ITfPu-x[0],thermal_high=(p.tFcl-x[1])-p.Kzru*p.tFcl,thermal_low=x[1]-p.tFcl,
                    above=p.KAct*x[0]-x[2]-band,below=x[2]-p.KAct*x[0]-p.IThOffPu-band,upper_cap=x[2]-p.IInstPu+p.cap_tolerance_pu)

    def initial(self,input_pu,*,regulating_output_pu=None):
        if self.p.initialization=='current_regulating_equilibrium':return self.regulating_equilibrium(input_pu,regulating_output_pu)
        if regulating_output_pu is not None:raise ValueError('regulating output only applies to current regulation')
        if self.p.initialization=='selected_equilibrium':
            from power_grid_env.oel2c_stationary import select_stationary
            x,self.context,self.initialization_audit=select_stationary(self.p,input_pu)
            rates=self.rhs(x,input_pu)
            if not np.isfinite(rates).all() or max(abs(rates))>1e-8:raise ValueError('selected active set is not a stationary OEL equilibrium')
            self.initialization_audit['maximum_derivative_per_s']=float(max(abs(rates)))
            return x
        if self.p.initialization=='inactive_equilibrium':return self.inactive_equilibrium(input_pu)
        if self.p.initialization=='active_limited_equilibrium':return self.active_limited_equilibrium(input_pu)
        p=self.p;m=p.KScale*input_pu;reference=p.KAct*m
        timer=(p.K2*((m/p.ITfPu)**p.C2-1)+(p.FixedRu if m<=p.ITfPu else p.FixedRd))/p.KFb
        if not np.isfinite([m,reference,timer]).all() or not p.ILimPu<=reference<=p.IInstPu or not p.tMin<=timer<=p.tMax:raise ValueError('OEL2C startup reference or timer outside declared limits')
        x=np.array([m,timer,reference,reference,0.,0.,0.,0.])
        c={k:v>=0 if k in ('scaled_low','thermal_high','thermal_low','upper_cap') else v>0 for k,v in self.criteria(x).items()}
        c.update(timer_bound=-1 if timer==p.tMin else 1 if timer==p.tMax else 0,reference_bound=-1 if reference==p.ILimPu else 1 if reference==p.IInstPu else 0,
                 enable_start=None,reset_start=None,enable_expired=False,reset_expired=False)
        self.context=c
        return x

    def inactive_equilibrium(self,input_pu):
        """Strict inactive upper-reference branch; explicit aged reset clock.

        No AVR reference or physical field-current base is adjusted. Bound-held
        reference/timer coordinates have zero projected rates. Other states
        solve the original continuous equations including PID anti-windup.
        """
        p=self.p;m=p.KScale*input_pu;actual=p.KAct*m
        margin=128*np.finfo(float).eps*max(1.,abs(m),p.ITfPu,p.IInstPu)
        if not np.isfinite([m,actual]).all() or m<0 or m>=p.ITfPu-margin or actual>=p.IInstPu-margin:
            raise ValueError('OEL2C inactive equilibrium requires strict subthermal current and instantaneous headroom')
        if abs(p.IInstPu-actual-p.IThOffPu)<=margin:
            raise ValueError('OEL2C equilibrium on reset-clock boundary is unsupported')
        ramp=p.K1*((m/p.ITfPu)**p.C1-1) if p.Sw1 else p.Kru
        if ramp<=0:raise ValueError('OEL2C inactive equilibrium requires an outward upper-reference ramp')
        target=p.K2*((m/p.ITfPu)**p.C2-1)
        timer=float(np.clip((np.clip(target,p.VInvMinPu,p.VInvMaxPu)+p.FixedRu)/p.KFb,p.tMin,p.tMax))
        if timer>=p.tFcl*(1-p.Kzru)-margin:
            raise ValueError('OEL2C inactive equilibrium requires strict thermal timer release headroom')
        x=np.array([m,timer,p.IInstPu,p.IInstPu,0.,0.,0.,0.])
        c={k:v>=0 if k in ('scaled_low','thermal_high','thermal_low','upper_cap') else v>0 for k,v in self.criteria(x).items()}
        c.update(timer_bound=-1 if timer==p.tMin else 1 if timer==p.tMax else 0,reference_bound=1,
                 enable_start=None,enable_expired=False,reset_start=-(p.tOff+1.) if c['below'] else None,reset_expired=bool(c['below']))
        self.context=c
        bias=p.IResetPu if c['below'] and p.tEn!=0 else 0.
        error=p.IInstPu-actual+bias
        x[4]=(.9-p.KpOel)*error;x[5]=p.KdOel/p.tDOel*error
        derivative=self.rhs(x,input_pu)
        if not np.isfinite(x).all() or not np.isfinite(derivative).all() or max(abs(derivative))>1e-8 or self.quantities(x)['output']!=0:
            raise ValueError('OEL2C inactive equilibrium failed projected-equation residual check')
        return x

    def active_limited_equilibrium(self,input_pu):
        """Strict hot, lower-reference, exhausted-PID-authority equilibrium."""
        p=self.p;m=p.KScale*input_pu;actual=p.KAct*m
        margin=128*np.finfo(float).eps*max(1.,abs(m),p.ITfPu,p.ILimPu)
        if not np.isfinite([m,actual]).all() or m<=p.ITfPu+margin or actual<=p.ILimPu+margin:
            raise ValueError('active OEL2C equilibrium requires strict thermal overload and lower-reference error')
        target=np.clip(p.K2*((m/p.ITfPu)**p.C2-1),p.VInvMinPu,p.VInvMaxPu)+p.FixedRd
        timer=float(np.clip(target/p.KFb,p.tMin,p.tMax))
        if timer<=p.tFcl+margin:raise ValueError('active OEL2C equilibrium requires strict hot timer headroom')
        ramp=p.K1*((m/p.ITfPu)**p.C1-1) if p.Sw1 else p.Krd
        if ramp>=0:raise ValueError('active OEL2C equilibrium requires a strictly downward reference ramp')
        error=p.ILimPu-actual;pid=p.VOel3MinPu
        lead=float(np.clip(pid,p.VOel2MinPu,0.));output=float(np.clip(lead,p.VOel1MinPu,0.))
        x=np.array([m,timer,p.ILimPu,p.ILimPu,pid+(.9-p.KpOel)*error,p.KdOel/p.tDOel*error,
                    (p.tC2Oel-p.tB2Oel)/p.tC2Oel*lead,(p.tC1Oel-p.tB1Oel)/p.tC1Oel*output])
        c={k:v>=0 if k in ('scaled_low','thermal_high','thermal_low','upper_cap') else v>0 for k,v in self.criteria(x).items()}
        c.update(timer_bound=-1 if timer==p.tMin else 1 if timer==p.tMax else 0,reference_bound=-1,
                 enable_start=-(p.tEn+1.),enable_expired=True,reset_start=None,reset_expired=False)
        self.context=c;derivative=self.rhs(x,input_pu)
        if not np.isfinite(x).all() or not np.isfinite(derivative).all() or max(abs(derivative))>1e-8 or abs(self.quantities(x)['output']-output)>1e-12:
            raise ValueError('active OEL2C equilibrium failed projected-equation residual check')
        return x

    def regulating_equilibrium(self,input_pu,output):
        p=self.p;m=p.KScale*input_pu;actual=p.KAct*m
        eps=128*np.finfo(float).eps*max(1.,abs(m),p.ITfPu,p.ILimPu)
        if output is None or not np.isfinite([m,actual,output]).all() or m<=p.ITfPu+eps or abs(actual-p.ILimPu)>min(1e-9,p.current_comparison_tolerance_pu/4):
            raise ValueError('regulating equilibrium requires solved field current and strict hot current branch')
        target=np.clip(p.K2*((m/p.ITfPu)**p.C2-1),p.VInvMinPu,p.VInvMaxPu)+p.FixedRd
        timer=float(np.clip(target/p.KFb,p.tMin,p.tMax));ramp=p.K1*((m/p.ITfPu)**p.C1-1) if p.Sw1 else p.Krd
        if timer<=p.tFcl+eps or ramp>=0:raise ValueError('regulating equilibrium requires hot timer and downward reference ramp')
        if timer==p.tMax and target-p.KFb*timer<=eps:raise ValueError('regulating timer bound has no strict outward margin')
        if not max(p.VOel1MinPu,p.VOel2MinPu,p.VOel3MinPu)+1e-8<output<-1e-8:raise ValueError('specified AVR reference requires unavailable or boundary OEL authority')
        error=p.ILimPu-actual
        x=np.array([m,timer,p.ILimPu,p.ILimPu,output-p.KpOel*error,p.KdOel/p.tDOel*error,(1-p.tB2Oel/p.tC2Oel)*output,(1-p.tB1Oel/p.tC1Oel)*output])
        c={k:v>=0 if k in ('scaled_low','thermal_high','thermal_low','upper_cap') else v>0 for k,v in self.criteria(x).items()}
        c.update(timer_bound=1 if timer==p.tMax else 0,reference_bound=-1,enable_start=None,enable_expired=False,reset_start=None,reset_expired=False)
        self.context=c;rates=self.rhs(x,input_pu)
        if not np.isfinite(rates).all() or max(abs(rates))>1e-8:raise ValueError('regulating equilibrium failed continuous residual check')
        return x

    def quantities(self,x):
        p=self.p;c=self.context;scaled=x[0]/p.ITfPu
        inverse=float(np.clip(p.K2*(scaled**p.C2-1),p.VInvMinPu,p.VInvMaxPu))
        target=inverse+(p.FixedRu if c['scaled_low'] else p.FixedRd)
        ramp=p.K1*(scaled**p.C1-1) if p.Sw1 else (p.Kru if c['thermal_high'] else p.Krd)
        if not c['thermal_high'] and not c['thermal_low']:ramp=0.
        bias=0. if c['thermal_low'] or c['enable_expired'] or p.tEn==0 else p.IResetPu if c['upper_cap'] and c['reset_expired'] else 0.
        error=x[3]-p.KAct*x[0]+bias
        differential=p.KdOel/p.tDOel*error-x[5]
        raw=p.KpOel*error+x[4]+differential;pid=float(np.clip(raw,p.VOel3MinPu,p.VOel3MaxPu))
        lead2=float(np.clip(p.tC2Oel/p.tB2Oel*(pid-x[6]),p.VOel2MinPu,p.VOel2MaxPu))
        output=float(np.clip(p.tC1Oel/p.tB1Oel*(lead2-x[7]),p.VOel1MinPu,p.VOel1MaxPu))
        return dict(inverse=inverse,target=target,ramp=ramp,bias=bias,error=error,differential=differential,raw=raw,pid=pid,lead2=lead2,output=output)

    def rhs(self,x,input_pu):
        p=self.p;q=self.quantities(x)
        rate=np.array([(p.KScale*input_pu-x[0])/p.tROel,q['target']-p.KFb*x[1],q['ramp'],(x[2]-x[3])/p.tAOel,
            p.KiOel*(q['error']+(q['pid']-q['raw'])/.9),(p.KdOel/p.tDOel*q['error']-x[5])/p.tDOel,
            ((p.tC2Oel-p.tB2Oel)/p.tC2Oel*q['lead2']-x[6])/p.tC2Oel,
            ((p.tC1Oel-p.tB1Oel)/p.tC1Oel*q['output']-x[7])/p.tC1Oel])
        if self.context['timer_bound']:rate[1]=0.
        if self.context['reference_bound']:rate[2]=0.
        return rate

    def free_rates(self,x):
        q=self.quantities(x);return {'timer':q['target']-self.p.KFb*x[1],'reference':q['ramp']}

    def guards(self,x):
        result=[];eps=64*np.finfo(float).eps
        for name in self.criteria(x):
            positive=self.context[name]
            def crossing(t,y,name=name,positive=positive):return self.criteria(y)[name]+(eps if positive else -eps)
            crossing.direction=-1 if positive else 1;crossing.terminal=True;result.append(('branch_'+name,crossing))
        for label,a,lo,hi in [('timer',1,self.p.tMin,self.p.tMax),('reference',2,self.p.ILimPu,self.p.IInstPu)]:
            mode=self.context[label+'_bound']
            if mode:
                def release(t,y,label=label,mode=mode):return self.free_rates(y)[label]+(-eps if mode<0 else eps)
                release.direction=1 if mode<0 else -1;release.terminal=True;result.append((label+'_release',release))
            else:
                for side,value,direction in [('lower',lo,-1),('upper',hi,1)]:
                    def bound(t,y,a=a,value=value):return y[a]-value
                    bound.direction=direction;bound.terminal=True;result.append((label+'_'+side,bound))
        for label,delay in [('enable',self.p.tEn),('reset',self.p.tOff)]:
            start=self.context[label+'_start']
            if start is None or self.context[label+'_expired'] or delay==0:continue
            def deadline(t,y,at=start+delay):return t-at
            deadline.direction=1;deadline.terminal=True;result.append((label+'_deadline',deadline))
        return result

    def apply(self,roots,time,x,log):
        c=self.context;eps=64*np.finfo(float).eps
        def branch(name,value):
            old=c[name];c[name]=bool(value)
            if old==c[name]:return
            if name in ('above','below'):
                label='enable' if name=='above' else 'reset'
                c[label+'_start']=float(time) if value else None;c[label+'_expired']=False
            log.append(dict(time_s=float(time),kind='oel2c_transition',transition=name,value=bool(value)))
        for root in sorted(roots):
            if root.startswith('branch_'):branch(root[7:],not c[root[7:]])
            elif root.endswith('_deadline'):
                label=root[:-9]
                # A simultaneous branch cancellation resets its timer first.
                # A stale deadline must not latch an inactive clock.
                if c[label+'_start'] is not None:
                    c[label+'_expired']=True;log.append(dict(time_s=float(time),kind='oel2c_transition',transition=root,value=True))
            else:
                label,side=root.split('_');a,lo,hi=(1,self.p.tMin,self.p.tMax) if label=='timer' else (2,self.p.ILimPu,self.p.IInstPu)
                if side=='release':c[label+'_bound']=0
                else:x[a]=lo if side=='lower' else hi;c[label+'_bound']=-1 if side=='lower' else 1
                log.append(dict(time_s=float(time),kind='oel2c_transition',transition=root,value=float(x[a])))
        for name,value in self.criteria(x).items():
            if (not c[name] and value>eps) or (c[name] and value < -eps):branch(name,not c[name])
        for label,delay in [('enable',self.p.tEn),('reset',self.p.tOff)]:
            if c[label+'_start'] is not None and not c[label+'_expired'] and time>=c[label+'_start']+delay:
                c[label+'_expired']=True;log.append(dict(time_s=float(time),kind='oel2c_transition',transition=label+'_deadline',value=True))
        rates=self.free_rates(x)
        for label,a,lo,hi in [('timer',1,self.p.tMin,self.p.tMax),('reference',2,self.p.ILimPu,self.p.IInstPu)]:
            mode=c[label+'_bound'];rate=rates[label];new=mode
            if mode and (rate>eps if mode<0 else rate < -eps):new=0
            if not new:
                if x[a]<=lo+eps and rate<0:new=-1;x[a]=lo
                elif x[a]>=hi-eps and rate>0:new=1;x[a]=hi
            if new!=mode:
                c[label+'_bound']=new;log.append(dict(time_s=float(time),kind='oel2c_transition',transition=label+'_mode',value=new))

    def signals(self,x):
        q=self.quantities(x);c=self.context
        values=list(x)+[self.p.KAct*x[0],self.p.tFcl-x[1],q['inverse'],q['target'],q['ramp'],q['bias'],q['error'],q['raw'],q['pid'],q['lead2'],q['output'],
            c['enable_start'] is not None,c['enable_expired'],c['reset_start'] is not None,c['reset_expired'],
            -1. if c['enable_start'] is None else c['enable_start'],-1. if c['reset_start'] is None else c['reset_start']]
        return {key:float(value) for key,value in zip(CHANNELS,values)}


class OEL2CBank:
    def __init__(self,model,offset):
        self.model=model;self.offset=offset;self.entries={};self.excursions={}
        ids={g.id:i for i,g in enumerate(model.generators)}
        for p in model.settings.oel2c_limiters:
            self.entries[ids[p.generator_id]]=(offset,p,OEL2CDynamics(p));offset+=8
        self.end=offset

    def inputs(self,state,current):
        if not self.entries:return {}
        m=self.model;rotor=state[m.gen_offset:m.rotor_end].reshape(-1,2)
        reaction=m.round_rotors.field_reaction(state,rotor,current)
        return {i:abs(reaction[i])/p.rated_field_reaction_pu for i,(_,p,_) in self.entries.items()}

    def initialize(self,state,current):
        for i,value in self.inputs(state,current).items():
            a,p,k=self.entries[i]
            try:
                output=self.model.controllers.vref[i]-p.specified_avr_reference_pu if p.initialization=='current_regulating_equilibrium' else None
                state[a:a+8]=k.initial(value,regulating_output_pu=output)
            except (ValueError,ArithmeticError) as e:
                result=dict(reason='oel2c_initialization_outside_profile',generator=p.generator_id,error=str(e))
                if hasattr(e,'audit'):result['boundary_audit']=e.audit
                return result
        return None

    def coordinate_initial_avr_reference(self,state):
        audit={}
        for i,(a,p,k) in self.entries.items():
            if p.initialization not in ('active_limited_equilibrium','current_regulating_equilibrium','selected_equilibrium'):continue
            if p.avr_reference_initialization not in ('derive_from_operating_point_with_OEL','specified_for_current_regulation'):raise ValueError('explicit active AVR reference policy required')
            baseline=self.model.controllers.vref[i];bias=k.quantities(state[a:a+8])['output']
            derived=p.specified_avr_reference_pu if p.initialization=='current_regulating_equilibrium' else baseline-bias
            if not np.isfinite(derived):raise ValueError('nonfinite OEL2C-coordinated AVR reference')
            self.model.controllers.vref[i]=derived
            audit[p.generator_id]=dict(policy=p.avr_reference_initialization,baseline_reference_pu=baseline,
                limiter_output_pu=bias,derived_reference_pu=derived,
                control_current_pu=float(p.KAct*state[a]),thermal_reference_pu=p.ILimPu,
                current_excess_pu=float(p.KAct*state[a]-p.ILimPu),PID_authority_exhausted=bool(p.initialization=='active_limited_equilibrium' or p.initialization=='selected_equilibrium' and k.quantities(state[a:a+8])['error'] < -1e-10 and k.quantities(state[a:a+8])['pid']<=p.VOel3MinPu+1e-10),specified_reference_pu=p.specified_avr_reference_pu)
        return audit

    def output(self,state):
        result=np.zeros(len(self.model.generators))
        for i,(a,p,k) in self.entries.items():result[i]=k.quantities(state[a:a+8])['output']
        return result

    def derivatives(self,state,current):
        result=np.zeros(self.end-self.offset)
        for i,value in self.inputs(state,current).items():
            a,p,k=self.entries[i];result[a-self.offset:a-self.offset+8]=k.rhs(state[a:a+8],value)
        return result

    def guards(self,state):
        ids=[];guards=[]
        for i,(a,p,k) in self.entries.items():
            for name,guard in k.guards(state[a:a+8]):
                def lifted(t,x,a=a,guard=guard):return guard(t,x[a:a+8])
                lifted.direction=guard.direction;lifted.terminal=True;ids.append(('oel2c',i,name));guards.append(lifted)
        return ids,guards

    def apply(self,roots,time,state,event_log):
        from power_grid_env.oel2c_events import coalesce_below_clocks
        if self.model.settings.event_policy=='legacy':roots=coalesce_below_clocks(self,roots,state,time)
        entries=self.entries.items()
        if self.entries and all(p.coincidence_policy=='independent_below_clock_batch_v1' for _,p,_ in self.entries.values()):
            entries=sorted(entries,key=lambda entry:entry[1][1].generator_id)
        for i,(a,p,k) in entries:
            logs=[];k.apply({r[2] for r in roots if r[0]=='oel2c' and r[1]==i},time,state[a:a+8],logs)
            event_log.extend(dict(e,equipment_id=p.generator_id) for e in logs)

    def difference_step(self,state,address,step):
        def regions(k,x):
            q=k.quantities(x);p=k.p
            raw=[q['raw'],p.tC2Oel/p.tB2Oel*(q['pid']-x[6]),p.tC1Oel/p.tB1Oel*(q['lead2']-x[7])]
            return tuple((v<=lo,v>=0.) for v,lo in zip(raw,[p.VOel3MinPu,p.VOel2MinPu,p.VOel1MinPu]))
        for a,p,k in self.entries.values():
            if p.initialization!='current_regulating_equilibrium' or not a<=address<a+8:continue
            x=state[a:a+8];original=regions(k,x)
            for _ in range(30):
                plus=x.copy();minus=x.copy();plus[address-a]+=step;minus[address-a]-=step
                if regions(k,plus)==original and regions(k,minus)==original:return step
                step*=.5
            raise ValueError('OEL2C conditional Jacobian is too close to a clipping boundary')
        return step

    def channel_names(self):return [p.generator_id+'.OEL2C.'+key for _,p,_ in self.entries.values() for key in (*CHANNELS,'field_reaction_pu','field_current_input_pu')]

    def signals(self,state,current):
        result={}
        inputs=self.inputs(state,current)
        reaction=self.model.excitation_limiters.reaction(state,current) if self.entries else []
        for i,(a,p,k) in self.entries.items():
            result.update({p.generator_id+'.OEL2C.'+key:value for key,value in k.signals(state[a:a+8]).items()})
            result[p.generator_id+'.OEL2C.field_reaction_pu']=float(reaction[i])
            result[p.generator_id+'.OEL2C.field_current_input_pu']=float(inputs[i])
        return result

    def observe(self,states):
        for i,(a,p,_) in self.entries.items():
            excursion=max(float(np.max(p.tMin-states[a+1])),float(np.max(states[a+1]-p.tMax)),float(np.max(p.ILimPu-states[a+2])),float(np.max(states[a+2]-p.IInstPu)))
            self.excursions[i]=max(self.excursions.get(i,0.),excursion)

    def validate(self,channels,rtol,atol):
        checks={}
        for i,(a,p,_) in self.entries.items():
            prefix=p.generator_id+'.OEL2C.';timer=np.asarray(channels[prefix+'timer_s']);reference=np.asarray(channels[prefix+'reference_current_pu']);output=np.asarray(channels[prefix+'output_pu'])
            tolerance=max(20*atol,20*rtol*max(1.,abs(p.tMin),abs(p.tMax),p.IInstPu))
            excess=max(0.,self.excursions.get(i,0.),float(np.max(p.tMin-timer)),float(np.max(timer-p.tMax)),float(np.max(p.ILimPu-reference)),float(np.max(reference-p.IInstPu)))
            checks[p.generator_id]=dict(within_limits=bool(np.isfinite(timer).all() and np.isfinite(reference).all() and np.isfinite(output).all() and excess<=tolerance and np.all(output>=p.VOel1MinPu) and np.all(output<=0.)),maximum_raw_integrator_overshoot=excess,tolerance=tolerance)
            actual=np.asarray(channels[prefix+'actual_current_pu']);pid=np.asarray(channels[prefix+'pid_output_pu']);error=np.asarray(channels[prefix+'regulator_error_pu'])
            checks[p.generator_id].update(limits_scope='output_and_integrator_bounds; not field-current acceptance',
                maximum_thermal_reference_excess_pu=float(max(0.,np.max(actual-p.ILimPu))),
                final_thermal_reference_excess_pu=float(max(0.,actual[-1]-p.ILimPu)),
                PID_lower_bound_with_negative_error_samples=int(np.count_nonzero((pid==p.VOel3MinPu)&(error<0))),
                current_acceptance_evaluated=False,diagnostic_sampling='saved samples including event sides; counts are not durations')

        return dict(ok=all(c['within_limits'] for c in checks.values()),limits=checks)
