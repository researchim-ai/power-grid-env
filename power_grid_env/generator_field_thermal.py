"""Rotor I² replica on explicitly normalized GENROU field reaction.

Trip opens the stator and forces impressed field voltage to zero. Rotor flux
and thermal memory stay continuous; field current does not vanish instantly.
No field-circuit opening, discharge resistor, OEM curve or temperature claim.
"""
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field


class GeneratorFieldThermalProtection(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    generator_id: str=Field(min_length=1)
    model: Literal['field_i2_replica']='field_i2_replica'
    rated_field_reaction_pu: float=Field(gt=0)
    time_constant_s: float=Field(gt=0)
    trip_level: float=Field(gt=0)
    initial_level: float=Field(default=0.,ge=0)
    initialization: Literal['specified','equilibrium']='specified'
    action: Literal['stator_open_zero_field_voltage']='stator_open_zero_field_voltage'


class FieldThermalBank:
    def __init__(self,model,offset):
        self.model=model;self.offset=offset;self.entries={};self.latched={}
        self.field_enabled=np.ones(len(model.generators),dtype=bool)
        indices={g.id:i for i,g in enumerate(model.generators)}
        for p in model.settings.generator_field_thermal:
            i=indices[p.generator_id];self.entries[i]=(offset,p);self.latched[i]=False;offset+=1
        self.end=offset

    def reaction(self,state,current):
        m=self.model;rotor=state[m.gen_offset:m.rotor_end].reshape(-1,2)
        return m.round_rotors.field_reaction(state,rotor,current)

    def initialize(self,state,current):
        if not self.entries:return None
        reaction=self.reaction(state,current)
        for i,(a,p) in self.entries.items():
            level=(abs(reaction[i])/p.rated_field_reaction_pu)**2 if p.initialization=='equilibrium' else p.initial_level
            if not np.isfinite(level) or level>=p.trip_level:return dict(reason='generator_initial_field_thermal_limit',generator=p.generator_id)
            state[a]=level
        return None

    def derivatives(self,state,current):
        if not self.entries:return np.empty(0)
        reaction=self.reaction(state,current)
        return np.array([((abs(reaction[i])/p.rated_field_reaction_pu)**2-state[a])/p.time_constant_s for i,(a,p) in self.entries.items()])

    def guards(self):
        ids=[];guards=[]
        for i,(a,p) in self.entries.items():
            if self.latched[i]:continue
            def crossing(t,x,a=a,p=p):return x[a]-p.trip_level
            crossing.terminal=True;crossing.direction=1;ids.append(('field',i));guards.append(crossing)
        return ids,guards

    def apply(self,roots,time,state,event_log,reaction):
        selected={key[1] for key in roots if key[0]=='field'}
        if roots:
            for i,(a,p) in self.entries.items():
                eps=64*np.finfo(float).eps*max(abs(state[a]),p.trip_level,np.finfo(float).tiny)
                if not self.latched[i] and state[a]>=p.trip_level-eps and (abs(reaction[i])/p.rated_field_reaction_pu)**2>state[a]:selected.add(i)
        for i in sorted(selected):
            if self.latched[i]:continue
            a,p=self.entries[i];self.latched[i]=True
            was_closed=bool(self.model.gen_connected[i]);self.model.gen_connected[i]=False;self.field_enabled[i]=False
            event_log.append(dict(time_s=float(time),kind='generator_field_thermal_trip',equipment_id=p.generator_id,
                                  level=float(state[a]),threshold=p.trip_level,action=p.action,stator_was_closed=was_closed,outcome='latched_zero_field_voltage'))

    def channel_names(self):
        return [p.generator_id+'.'+k for _,p in self.entries.values() for k in ('field_thermal_level','field_thermal_latched','field_current_pu','field_voltage_enabled')]

    def signals(self,state,current):
        if not self.entries:return {}
        reaction=self.reaction(state,current);result={}
        for i,(a,p) in self.entries.items():
            row=dict(field_thermal_level=state[a],field_thermal_latched=self.latched[i],field_current_pu=abs(reaction[i])/p.rated_field_reaction_pu,field_voltage_enabled=self.field_enabled[i])
            result.update({p.generator_id+'.'+k:float(v) for k,v in row.items()})
        return result
