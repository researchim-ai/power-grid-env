"""Declared stator RMS-I-squared replica with a latched electrical trip.

A positive-sequence effective model, not winding temperature, negative-sequence
heating, an OEM capability curve, or an excitation/current clipping algorithm.
"""
from __future__ import annotations
import math
import numpy as np
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal


class GeneratorThermalProtection(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    generator_id: str=Field(min_length=1)
    model: Literal['stator_i2_replica']='stator_i2_replica'
    time_constant_s: float=Field(gt=0)
    trip_level: float=Field(gt=0)
    initial_level: float=Field(default=0.,ge=0)
    initialization: Literal['specified','equilibrium']='specified'


class GeneratorThermalBank:
    def __init__(self,model,offset):
        self.model=model;self.offset=offset;self.entries={};self.latched={}
        indices={g.id:i for i,g in enumerate(model.generators)}
        for p in model.settings.generator_thermal:
            i=indices[p.generator_id];self.entries[i]=(offset,p);self.latched[i]=False;offset+=1
        self.end=offset

    def initialize(self,state,current):
        for i,(a,p) in self.entries.items():
            state[a]=abs(current[i])**2 if p.initialization=='equilibrium' else p.initial_level
            if not math.isfinite(state[a]) or state[a]>=p.trip_level:return dict(reason='generator_initial_thermal_limit',generator=p.generator_id)
        return None

    def derivatives(self,state,current):
        return np.array([(abs(current[i])**2-state[a])/p.time_constant_s for i,(a,p) in self.entries.items()])

    def guards(self):
        ids=[];guards=[]
        for i,(a,p) in self.entries.items():
            if self.latched[i] or not self.model.gen_connected[i]:continue
            def crossing(t,x,a=a,p=p):return x[a]-p.trip_level
            crossing.terminal=True;crossing.direction=1;ids.append(i);guards.append(crossing)
        return ids,guards

    def apply(self,triggered,time,state,event_log):
        if not triggered:return
        voltage=self.model.electrical(state)[0];current=self.model.generator_currents(state,voltage)
        selected=set(triggered)
        # Include only roundoff-level ties with a rising heat state; never
        # coalesce distinct relays using the much wider integration tolerance.
        for i,(a,p) in self.entries.items():
            eps=64*np.finfo(float).eps*max(abs(state[a]),p.trip_level,np.finfo(float).tiny)
            if not self.latched[i] and self.model.gen_connected[i] and state[a]>=p.trip_level-eps and abs(current[i])**2>state[a]:selected.add(i)
        for i in sorted(selected):
            if self.latched[i] or not self.model.gen_connected[i]:continue
            a,p=self.entries[i];self.latched[i]=True;self.model.gen_connected[i]=False
            event_log.append(dict(time_s=float(time),kind='generator_thermal_trip',equipment_id=p.generator_id,level=float(state[a]),threshold=p.trip_level,outcome='latched_electrical_open'))

    def channel_names(self):return [p.generator_id+'.'+k for _,p in self.entries.values() for k in ('stator_thermal_level','stator_thermal_latched')]

    def signals(self,state):
        return {p.generator_id+'.'+k:float(v) for i,(a,p) in self.entries.items() for k,v in [('stator_thermal_level',state[a]),('stator_thermal_latched',self.latched[i])]}
