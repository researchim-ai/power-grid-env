"""Hysteretic voltage-contactor logic with exact deadlines and voltage roots.

An effective RMS control model, not electromagnetic coil/armature dynamics.
Initially open motors never auto-start; manual open inhibits auto restart.
"""
from dataclasses import dataclass
import math
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator


class MotorContactorControl(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    model: Literal['hysteretic_voltage_contactor']='hysteretic_voltage_contactor'
    sense_node_id: str=Field(min_length=1)
    dropout_voltage_pu: float=Field(gt=0,lt=2)
    pickup_voltage_pu: float=Field(gt=0,le=2)
    dropout_delay_s: float=Field(ge=1e-6,le=60)
    pickup_delay_s: float=Field(ge=1e-6,le=60)
    minimum_off_time_s: float=Field(ge=0,le=60)
    maximum_automatic_restarts: int=Field(ge=0,le=10)

    @model_validator(mode='after')
    def hysteresis(self):
        if self.pickup_voltage_pu<=self.dropout_voltage_pu:raise ValueError('pickup must exceed dropout voltage')
        return self


class ContactorControlError(ValueError):
    pass


@dataclass
class ContactorState:
    available: bool
    since: float
    pending_restart: bool=False
    opened_at: float=0.
    restarts: int=0


class MotorContactorBank:
    def __init__(self,model,state):
        self.model=model;self.states={};self.sense={};self.transitions=0
        voltage=model.electrical(state)[0]
        for i,motor in enumerate(model.settings.motors):
            if motor.contactor is None:continue
            bus=model.topology.node_to_bus[motor.contactor.sense_node_id]
            self.sense[i]=model.bus_names.index(bus)
            v=abs(voltage[self.sense[i]]);p=motor.contactor
            available=bool(v>p.dropout_voltage_pu if model.connected[i] else v>=p.pickup_voltage_pu)
            self.states[i]=ContactorState(available,0.)

    def log(self,log,time,i,kind,**fields):
        self.transitions+=1
        if self.transitions>self.model.settings.maximum_contactor_transitions:raise ContactorControlError('contactor transition budget exceeded')
        log.append(dict(time_s=float(time),equipment_id=self.model.settings.motors[i].id,kind=kind,**fields))

    def channels(self):
        return {self.model.settings.motors[i].id+'.'+key:[] for i in self.states for key in ['contactor_sensed_voltage_pu','contactor_voltage_available','contactor_pending_restart','contactor_restart_count','contactor_timer_active','contactor_timer_remaining_s']}

    def signals(self,voltage,time):
        return {self.model.settings.motors[i].id+'.'+key:float(value) for i,s in self.states.items() for key,value in dict(contactor_sensed_voltage_pu=abs(voltage[self.sense[i]]),contactor_voltage_available=s.available,contactor_pending_restart=s.pending_restart,contactor_restart_count=s.restarts,contactor_timer_active=math.isfinite(self.deadline(i)),contactor_timer_remaining_s=max(0.,self.deadline(i)-time) if math.isfinite(self.deadline(i)) else 0.).items()}

    def guards(self):
        ids=[];guards=[]
        for i,s in self.states.items():
            p=self.model.settings.motors[i].contactor;threshold=p.dropout_voltage_pu if s.available else p.pickup_voltage_pu
            def crossing(time,state,i=i,threshold=threshold):return abs(self.model.electrical(state)[0][self.sense[i]])-threshold
            crossing.direction=-1 if s.available else 1;crossing.terminal=True
            ids.append(i);guards.append(crossing)
        return ids,guards

    def deadline(self,i):
        s=self.states[i];p=self.model.settings.motors[i].contactor
        if self.model.thermal_latched[i]:return math.inf
        if self.model.connected[i] and not s.available:return s.since+p.dropout_delay_s
        if not self.model.connected[i] and s.available and s.pending_restart and s.restarts<p.maximum_automatic_restarts:
            return max(s.since+p.pickup_delay_s,s.opened_at+p.minimum_off_time_s)
        return math.inf

    def next_deadline(self,right):return min([right,*[self.deadline(i) for i in self.states]])

    def due(self,time):return any(self.deadline(i)<=time+8*np.finfo(float).eps*max(1.,abs(time)) for i in self.states)

    def manual(self,i,kind,time):
        if i not in self.states:return
        s=self.states[i]
        if kind=='motor_open':s.pending_restart=False;s.opened_at=time
        elif kind=='motor_close':s.pending_restart=False

    def close_permitted(self,i):return i not in self.states or self.states[i].available

    def observe(self,time,state,log,roots=()):
        voltage=self.model.electrical(state)[0]
        for i,s in self.states.items():
            p=self.model.settings.motors[i].contactor;v=abs(voltage[self.sense[i]])
            epsilon=64*np.finfo(float).eps*max(1.,v,p.pickup_voltage_pu)
            change=i in roots or (s.available and v<=p.dropout_voltage_pu+epsilon) or (not s.available and v>=p.pickup_voltage_pu-epsilon)
            if change:
                s.available=not s.available;s.since=time
                self.log(log,time,i,'contactor_voltage_available' if s.available else 'contactor_voltage_unavailable',sensed_voltage_pu=float(v),outcome='hysteresis_transition')
            if self.model.thermal_latched[i] and s.pending_restart:
                s.pending_restart=False;self.log(log,time,i,'contactor_restart_cancelled',outcome='thermal_latch')

    def actions(self,time):
        return [(i,'motor_close' if self.states[i].available else 'motor_open') for i in self.states if self.deadline(i)<=time+8*np.finfo(float).eps*max(1.,abs(time))]

    def applied(self,i,kind,time,log,diagnostic=None):
        s=self.states[i]
        if kind=='motor_open':
            s.opened_at=time;s.pending_restart=s.restarts<self.model.settings.motors[i].contactor.maximum_automatic_restarts
            self.log(log,time,i,kind,outcome='applied',cause='undervoltage_timer',restart_eligible=bool(s.pending_restart),restart_count=s.restarts)
        else:
            s.restarts+=1;s.pending_restart=False
            self.log(log,time,i,kind,outcome='applied',cause='automatic_restart',reclose_diagnostic=diagnostic)
