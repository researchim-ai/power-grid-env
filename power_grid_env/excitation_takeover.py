"""Declared absolute-field PI OEL, with sensor lag and back-calculation.

This is an explicit engineering profile, not an IEEE OEL1/OEL2 implementation.
The independent oracle composes unchanged MSL FirstOrder/Integrator/Limiter.
"""
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator

STATES=('filtered_current_pu','integrator_pu')
VALUES=('field_current_pu','error_pu','raw_ceiling_pu','ceiling_pu','lower_active','upper_active')

class TakeoverOELPassport(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    generator_id: str=Field(min_length=1)
    model: Literal['FieldCurrentPI_AbsoluteTakeover_v1']='FieldCurrentPI_AbsoluteTakeover_v1'
    current_limit_pu: float=Field(default=1.2,gt=0)
    sensor_time_s: float=Field(default=.02,ge=1e-6)
    proportional_gain: float=Field(default=2.,gt=0)
    integral_gain: float=Field(default=10.,gt=0)
    tracking_time_s: float=Field(default=.1,ge=1e-6)
    minimum_field_pu: float=Field(default=0.,ge=0)
    maximum_field_pu: float=Field(default=4.,gt=0)

    @model_validator(mode='after')
    def ordered(self):
        if self.minimum_field_pu>=self.maximum_field_pu:raise ValueError('ordered absolute OEL field bounds required')
        return self


def quantities(p,state,current):
    error=p.current_limit_pu-state[0];raw=state[1]+p.proportional_gain*error
    ceiling=float(np.clip(raw,p.minimum_field_pu,p.maximum_field_pu))
    return dict(field_current_pu=float(current),error_pu=float(error),raw_ceiling_pu=float(raw),ceiling_pu=ceiling,
                lower_active=float(raw<p.minimum_field_pu),upper_active=float(raw>p.maximum_field_pu))


def rates(p,state,current):
    q=quantities(p,state,current)
    return np.array([(current-state[0])/p.sensor_time_s,p.integral_gain*q['error_pu']+(q['ceiling_pu']-q['raw_ceiling_pu'])/p.tracking_time_s])


class TakeoverOELBank:
    def __init__(self,model,offset):
        self.model=model;self.offset=offset;self.entries={}
        ids={g.id:i for i,g in enumerate(model.generators)}
        for p in model.settings.takeover_oels:
            self.entries[ids[p.generator_id]]=(offset,p);offset+=2
        self.end=offset

    def measurements(self,state,current=None):
        if not self.entries:return {}
        m=self.model
        if current is None:current=m.generator_currents(state,m.electrical(state)[0])
        rotor=state[m.gen_offset:m.rotor_end].reshape(-1,2)
        reaction=m.round_rotors.field_reaction(state,rotor,current)
        return {i:float(reaction[i]) for i in self.entries}

    def initialize(self,state,current,field):
        for i,value in self.measurements(state,current).items():
            a,p=self.entries[i];error=p.current_limit_pu-value
            if error<=1e-7 or p.integral_gain*p.tracking_time_s*error<=1e-8 or field[i]>=p.maximum_field_pu-1e-7:
                return dict(reason='initial_takeover_OEL_limit_or_boundary',generator=p.generator_id)
            state[a:a+2]=[value,p.maximum_field_pu+(p.integral_gain*p.tracking_time_s-p.proportional_gain)*error]
        return None

    def output(self,state):return {i:quantities(p,state[a:a+2],0.)['ceiling_pu'] for i,(a,p) in self.entries.items()}

    def derivatives(self,state,current):
        result=np.zeros(self.end-self.offset)
        for i,value in self.measurements(state,current).items():
            a,p=self.entries[i];result[a-self.offset:a-self.offset+2]=rates(p,state[a:a+2],value)
        return result

    def channel_names(self):return [p.generator_id+'.TakeoverOEL.'+k for a,p in self.entries.values() for k in (*STATES,*VALUES)]

    def signals(self,state,current):
        out={}
        for i,value in self.measurements(state,current).items():
            a,p=self.entries[i];row=dict(zip(STATES,state[a:a+2]))|quantities(p,state[a:a+2],value)
            out.update({p.generator_id+'.TakeoverOEL.'+k:float(v) for k,v in row.items()})
        return out


    def validate(self,channels):
        checks={}
        for a,p in self.entries.values():
            prefix=p.generator_id+'.TakeoverOEL.';out=np.asarray(channels[prefix+'ceiling_pu'])
            finite=all(np.isfinite(channels[prefix+k]).all() for k in (*STATES,*VALUES))
            checks[p.generator_id]=dict(finite=finite,within_bounds=bool(np.all(out>=p.minimum_field_pu) and np.all(out<=p.maximum_field_pu)))
        return dict(ok=all(c['finite'] and c['within_bounds'] for c in checks.values()),limits=checks)
