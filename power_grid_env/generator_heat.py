"""Dimensioned one-node generator thermal observer, separate from I2 relays.

Current/terminal voltage drive copper and quadratic core-loss heat. Temperature
feeds the copper-loss law only: these observed losses are NOT subtracted from
GENCLS electrical/shaft power. No electrical R(T) stamp or hotspot is implied.
"""
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from typing import Literal


class GeneratorHeat(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    generator_id:str=Field(min_length=1)
    capacity_j_per_k:float=Field(gt=0)
    ambient_w_per_k:float=Field(gt=0)
    resistance_ohm:float=Field(ge=0)
    core_loss_w:float=Field(default=0,ge=0)
    alpha_per_c:float=Field(default=0,ge=0)
    reference_c:float=20.
    ambient_c:float=Field(default=20.,ge=-273.15)
    initial_c:float=Field(default=20.,ge=-273.15)
    maximum_c:float=Field(default=200.,gt=-273.15)
    initialization:Literal['specified','equilibrium']='specified'
    provenance:str=Field(min_length=1)

    @model_validator(mode='after')
    def validity(self):
        if self.ambient_c>=self.maximum_c or self.initial_c>=self.maximum_c or min(1+self.alpha_per_c*(t-self.reference_c) for t in [self.ambient_c,self.initial_c])<=0:raise ValueError('invalid generator heat temperature range')
        return self


class GeneratorHeatBank:
    def __init__(self,model,offset):
        self.model=model;self.entries={};self.offset=offset
        lookup={g.id:i for i,g in enumerate(model.generators)}
        for p in model.settings.generator_heat:
            self.entries[lookup[p.generator_id]]=(offset,p);offset+=1
        self.end=offset

    def seed(self,state):
        for a,p in self.entries.values():state[a]=p.initial_c
        return state

    def terms(self,state,current,voltage,i,a,p):
        t=state[a];factor=1+p.alpha_per_c*(t-p.reference_c)
        if not np.isfinite(t) or t< -273.15 or t>p.maximum_c or factor<=0:raise ValueError('generator heat temperature outside passport validity')
        g=self.model.generators[i];m=self.model.gen_passports[i];ib=m.rated_va/(np.sqrt(3)*g.ll_v)
        copper=3*p.resistance_ohm*factor*abs(current[i]*ib)**2
        core=p.core_loss_w*abs(voltage[i])**2;removed=p.ambient_w_per_k*(t-p.ambient_c)
        return copper,core,removed

    def initialize(self,state,current,voltage,*,stationary=False):
        for i,(a,p) in self.entries.items():
            if stationary and p.initialization!='equilibrium':return dict(reason='joint_equilibrium_requires_generator_heat_equilibrium',generator=p.generator_id)
            if p.initialization=='equilibrium':
                g=self.model.generators[i];m=self.model.gen_passports[i];ib=m.rated_va/(np.sqrt(3)*g.ll_v)
                q=3*p.resistance_ohm*abs(current[i]*ib)**2;den=p.ambient_w_per_k-q*p.alpha_per_c
                if den<=0:return dict(reason='generator_heat_no_stable_equilibrium',generator=p.generator_id)
                state[a]=(q*(1-p.alpha_per_c*p.reference_c)+p.core_loss_w*abs(voltage[i])**2+p.ambient_w_per_k*p.ambient_c)/den
            else:state[a]=p.initial_c
            try:self.terms(state,current,voltage,i,a,p)
            except ValueError:return dict(reason='generator_heat_equilibrium_outside_validity',generator=p.generator_id)
        return None

    def derivatives(self,state,current,voltage):
        return np.array([(sum(self.terms(state,current,voltage,i,a,p)[:2])-self.terms(state,current,voltage,i,a,p)[2])/p.capacity_j_per_k for i,(a,p) in self.entries.items()])

    def actions(self,state,u,voltage,dv):
        result=np.zeros((self.end,u.shape[1]));m=self.model;eg=m.generator_internal(state);ig=m.generator_currents(state,voltage)
        from power_grid_env.genrou_jacobian import clip_slope
        from power_grid_env.generator_stator import impedance,impedance_product
        gz=impedance(m,state);dz=impedance_product(m,u)
        for i,(a,p) in self.entries.items():
            delta=m.gen_offset+2*i;de=1j*eg[i]*u[delta]
            if i in m.controllers.avr:
                _,field=m.controllers.avr[i];e=m.gen_passports[i].exciter
                de+=clip_slope(state[field],e.EMIN,e.EMAX)*u[field]*np.exp(1j*state[delta])
            di=(de-dv[m.gen_buses[i]]-ig[i]*dz[i])/gz[i] if m.gen_connected[i] else np.zeros(u.shape[1],complex)
            terminal=voltage[m.gen_buses[i]] if m.gen_connected[i] else eg[i];dt=dv[m.gen_buses[i]] if m.gen_connected[i] else de
            ib=m.gen_passports[i].rated_va/(np.sqrt(3)*m.generators[i].ll_v);factor=1+p.alpha_per_c*(state[a]-p.reference_c)
            result[a]=(6*p.resistance_ohm*ib**2*factor*(np.conj(ig[i])*di).real+(3*p.resistance_ohm*ib**2*abs(ig[i])**2*p.alpha_per_c-p.ambient_w_per_k)*u[a]+2*p.core_loss_w*(np.conj(terminal)*dt).real)/p.capacity_j_per_k
        return result

    def signals(self,state,current,voltage):
        out={}
        for i,(a,p) in self.entries.items():
            copper,core,removed=self.terms(state,current,voltage,i,a,p)
            data=dict(temperature_c=state[a],copper_loss_w=copper,core_loss_w=core,ambient_heat_w=removed,net_heat_w=copper+core-removed)
            out.update({p.generator_id+'.heat.'+k:float(v) for k,v in data.items()})
        return out

    def channel_names(self):return [p.generator_id+'.heat.'+k for a,p in self.entries.values() for k in ['temperature_c','copper_loss_w','core_loss_w','ambient_heat_w','net_heat_w']]
