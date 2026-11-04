"""MNLEX2 circle limiter, explicitly matching OpenIPSL 3.1.0 lag semantics.

Equations derived from OpenIPSL (BSD-3-Clause), Luigi Vanfretti, ALSETLab
and contributors. See assets/licenses/OpenIPSL-LICENSE.txt. The clipped lag has
an unrestricted raw state and reinitializes at a bound when its rate turns
inward outside that bound. It is NOT a projected-derivative lag.
"""
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field


class UnderexcitationLimiter(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    generator_id: str=Field(min_length=1)
    model: Literal['MNLEX2_OpenIPSL_3_1_0']='MNLEX2_OpenIPSL_3_1_0'
    initialization: Literal['inactive_equilibrium','zero_output_startup']='inactive_equilibrium'
    event_detection: Literal['machine_epsilon','conditioned_1e-8']='machine_epsilon'
    K_F2: float=Field(default=.1,gt=0)
    T_F2: float=Field(default=1.,gt=0)
    K_M: float=Field(default=.3,gt=0)
    T_M: float=Field(default=.5,gt=0)
    MEL_MAX: float=Field(default=.1,gt=0)
    Q_0: float=2.5
    Radius: float=Field(default=3.,gt=0)


def reset_rate_floor(p):
    """Explicit event localization policy; continuous MNLEX2 ODE is unchanged."""
    return 1e-8 if p.event_detection=='conditioned_1e-8' else 64*np.finfo(float).eps


def circle_error(p,active,reactive,voltage):
    return active**2+(reactive-p.Q_0*voltage**2)**2-(p.Radius*voltage**2)**2


def mnlex2_rates(p,state,error):
    raw,memory=state
    output=np.clip(raw,0.,p.MEL_MAX)
    feedback=p.K_F2/p.T_F2*(output-memory)
    return np.array([(p.K_M*(error-feedback)-raw)/p.T_M,(output-memory)/p.T_F2])


class MinimumExcitationBank:
    def __init__(self,model,offset):
        self.model=model;self.offset=offset;self.entries={};self.regions={};self.memory_overshoot={}
        indices={g.id:i for i,g in enumerate(model.generators)}
        for p in model.settings.underexcitation_limiters:
            i=indices[p.generator_id];self.entries[i]=(offset,p);self.regions[i]=-1;offset+=2
        self.end=offset

    def measurements(self,state):
        if not self.entries:return {}
        m=self.model;v=m.electrical(state)[0];current=m.generator_currents(state,v)
        terminal=m.generator_terminal_voltage(state,v);power=terminal*np.conj(current)
        return {i:(float(power[i].real),float(power[i].imag),float(abs(terminal[i]))) for i in self.entries}

    def errors(self,state):
        return {i:circle_error(p,*values) for i,values in self.measurements(state).items() for _,p in [self.entries[i]]}

    def initialize(self,state):
        errors=self.errors(state)
        for i,error in errors.items():
            if not np.isfinite(error) or error>=-64*np.finfo(float).eps*max(1.,self.entries[i][1].Radius**2):
                return dict(reason='initial_underexcitation_limit_or_boundary',generator=self.model.generators[i].id)
        for i,(a,p) in self.entries.items():
            state[a:a+2]=[p.K_M*errors[i] if p.initialization=='inactive_equilibrium' else 0.,0.]
        return None

    def output(self,state):
        values=np.zeros(len(self.model.generators))
        for i,(a,p) in self.entries.items():values[i]=np.clip(state[a],0.,p.MEL_MAX)
        return values

    def derivatives(self,state):
        out=np.zeros(self.end-self.offset)
        for i,error in self.errors(state).items():
            a,p=self.entries[i];out[a-self.offset:a-self.offset+2]=mnlex2_rates(p,state[a:a+2],error)
        return out

    def free_rate(self,state,i):
        a,p=self.entries[i];return mnlex2_rates(p,state[a:a+2],self.errors(state)[i])[0]

    def guards(self,state):
        ids=[];guards=[]
        for i,(a,p) in self.entries.items():
            region=self.regions[i]
            if region:
                eps=reset_rate_floor(p);rate=self.free_rate(state,i)
                instant=rate>eps if region<0 else rate < -eps
                def reversal(t,x,i=i,region=region,instant=instant,eps=eps):
                    if instant:return 0.
                    return self.free_rate(x,i)+(-eps if region<0 else eps)
                reversal.terminal=True;reversal.direction=1 if region<0 else -1
                ids.append(('uel',i,'reset'));guards.append(reversal)
            else:
                for side,limit,direction in [('lower',0.,-1),('upper',p.MEL_MAX,1)]:
                    def entry(t,x,a=a,limit=limit):return x[a]-limit
                    entry.terminal=True;entry.direction=direction
                    ids.append(('uel',i,side));guards.append(entry)
        return ids,guards

    def apply(self,roots,time,state,event_log):
        for _,i,side in sorted(roots):
            a,p=self.entries[i];before=float(state[a]);region=self.regions[i]
            if side=='reset':
                state[a]=0. if region<0 else p.MEL_MAX
                rate=self.free_rate(state,i)
                self.regions[i]=(-1 if rate<=0. else 0) if region<0 else (1 if rate>=0. else 0)
            else:self.regions[i]=-1 if side=='lower' else 1
            event_log.append(dict(time_s=float(time),kind='uel_lag_reset' if side=='reset' else 'uel_lag_region_enter',
                equipment_id=p.generator_id,bound=('lower' if region<0 else 'upper') if side=='reset' else side,
                raw_state_before_pu=before,raw_state_after_pu=float(state[a]),event_detection=p.event_detection,event_rate_floor_per_s=float(reset_rate_floor(p))))

    def refresh(self,time,state,event_log):
        roots={('uel',i,'reset') for i,(a,p) in self.entries.items() if self.regions[i] and
               (self.free_rate(state,i)>reset_rate_floor(p) if self.regions[i]<0 else self.free_rate(state,i)<-reset_rate_floor(p))}
        self.apply(roots,time,state,event_log)

    def channel_names(self):
        return [p.generator_id+'.UEL.'+k for _,p in self.entries.values() for k in
                ('p_pu','q_pu','voltage_pu','circle_error_pu2','output_pu','raw_lag_state_pu','feedback_pu','feedback_memory_pu')]

    def signals(self,state):
        out={}
        for i,values in self.measurements(state).items():
            a,p=self.entries[i];output=np.clip(state[a],0.,p.MEL_MAX)
            signals=dict(p_pu=values[0],q_pu=values[1],voltage_pu=values[2],circle_error_pu2=circle_error(p,*values),
                         output_pu=output,raw_lag_state_pu=state[a],feedback_pu=p.K_F2/p.T_F2*(output-state[a+1]),feedback_memory_pu=state[a+1])
            out.update({p.generator_id+'.UEL.'+k:float(v) for k,v in signals.items()})
        return out

    def observe(self,states):
        for i,(a,p) in self.entries.items():
            v=states[a+1];self.memory_overshoot[i]=max(self.memory_overshoot.get(i,0.),float(np.max(-v)),float(np.max(v-p.MEL_MAX)))

    def validate(self,channels,rtol,atol):
        result={}
        for i,(a,p) in self.entries.items():
            prefix=p.generator_id+'.UEL.';raw=np.asarray(channels[prefix+'raw_lag_state_pu']);output=np.asarray(channels[prefix+'output_pu']);memory=np.asarray(channels[prefix+'feedback_memory_pu'])
            tolerance=max(20*atol,20*rtol*max(1.,p.MEL_MAX))
            excursion=max(0.,self.memory_overshoot.get(i,0.),float(np.max(-memory)),float(np.max(memory-p.MEL_MAX)))
            result[p.generator_id]=dict(within_limits=bool(np.isfinite(raw).all() and np.isfinite(output).all() and np.isfinite(memory).all() and np.all(output>=0) and np.all(output<=p.MEL_MAX) and excursion<=tolerance),
                maximum_feedback_memory_overshoot=excursion,tolerance=tolerance,raw_lag_state_is_intentionally_unbounded=True)
        return dict(ok=all(v['within_limits'] for v in result.values()),limits=result)
