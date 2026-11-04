"""OpenIPSL 3.1 ESST1A: VOS=1, UEL=1 and explicit absolute takeover ports.

Equations adapted from OpenIPSL (BSD-3-Clause), ALSETLab and contributors;
see assets/licenses/OpenIPSL-LICENSE.txt. The raw regulator lag winds beyond
its output limits and resets on inward reversal, as in SimpleLagLim.
"""
from typing import Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator

PORTS=('oel_ceiling_pu','uel_error_floor_pu','uel_field_floor_pu','uel_sum_pu','supplementary_voltage_pu','supplementary_field_pu')
STATES=('sensor_pu','lead1_memory_pu','lead2_memory_pu','regulator_raw_pu','washout_memory_pu')
VALUES=('effective_oel_ceiling_pu','effective_voltage_input_pu','field_pu','reference_pu','reference_increment_pu','field_current_pu','feedback_pu','error_pu','error_limited_pu','error_selected_pu','lead1_output_pu','lead2_output_pu','regulator_output_pu','current_limit_pu','field_demand_pu','field_floor_selected_pu','takeover_selected_pu','rectifier_lower_pu','rectifier_upper_pu','oel_active','uel2_active','uel3_active','rectifier_upper_active','rectifier_lower_active')

class ESST1APassport(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    generator_id: str=Field(min_length=1)
    model: Literal['ESST1A_OpenIPSL_3_1_0_VOS1_UEL1_external_takeover']='ESST1A_OpenIPSL_3_1_0_VOS1_UEL1_external_takeover'
    T_R: float=Field(default=.02,ge=1e-6)
    T_B: float=Field(default=.2,ge=1e-6)
    T_C: float=Field(default=.05,ge=0)
    T_B1: float=Field(default=.3,ge=1e-6)
    T_C1: float=Field(default=.07,ge=0)
    K_A: float=Field(default=10.,gt=0)
    T_A: float=Field(default=.05,ge=1e-6)
    V_IMIN: float=-.2
    V_IMAX: float=.2
    V_AMIN: float=Field(default=0.,ge=0)
    V_AMAX: float=4.
    V_RMIN: float=Field(default=0.,ge=0)
    V_RMAX: float=4.
    K_C: float=Field(default=.1,ge=0)
    K_F: float=Field(default=.02,ge=1e-6)
    T_F: float=Field(default=.5,ge=1e-6)
    K_LR: float=Field(default=.4,ge=0)
    I_LR: float=Field(default=1.5,ge=0)
    oel_ceiling_pu: float=Field(default=4.,ge=0)
    uel_error_floor_pu: float=-.2
    uel_field_floor_pu: float=Field(default=0.,ge=0)

    @model_validator(mode='after')
    def ranges(self):
        for low,high in [(self.V_IMIN,self.V_IMAX),(self.V_AMIN,self.V_AMAX),(self.V_RMIN,self.V_RMAX)]:
            if not low<high:raise ValueError('ordered ESST1A limits required')
        if min(abs(self.T_C-self.T_B),abs(self.T_C1-self.T_B1))<1e-10:raise ValueError('equal-time-constant bypass is outside the five-state ESST1A profile')
        return self

    def native_parameters(self):return self.model_dump(exclude={'generator_id','model','oel_ceiling_pu','uel_error_floor_pu','uel_field_floor_pu'})


def quantities(p,x,voltage,current,vref,ports,uel=0.):
    sensor,z1,z2,raw,zf=x
    limited=float(np.clip(raw,p.V_AMIN,p.V_AMAX))
    current_limit=max(0.,p.K_LR*(current-p.I_LR))
    demand=(ports['supplementary_field_pu']+limited)-current_limit
    feedback=p.K_F/p.T_F*(demand-zf)
    error=(-feedback+(vref-sensor))+(ports['supplementary_voltage_pu']+(ports['uel_sum_pu']+uel))
    clipped=float(np.clip(error,p.V_IMIN,p.V_IMAX));selected=max(clipped,ports['uel_error_floor_pu'])
    lead1=p.T_C/p.T_B*selected+(1-p.T_C/p.T_B)*z1
    lead2=p.T_C1/p.T_B1*lead1+(1-p.T_C1/p.T_B1)*z2
    high=max(demand,ports['uel_field_floor_pu']);takeover=min(high,ports['oel_ceiling_pu'])
    lower=voltage*p.V_RMIN;upper=voltage*p.V_RMAX-p.K_C*current
    if not np.isfinite([lower,upper]).all() or upper<lower:raise ValueError('ESST1A rectifier limits inverted or nonfinite')
    return dict(effective_oel_ceiling_pu=ports['oel_ceiling_pu'],effective_voltage_input_pu=ports['supplementary_voltage_pu'],field_pu=float(np.clip(takeover,lower,upper)),feedback_pu=feedback,error_pu=error,error_limited_pu=clipped,error_selected_pu=selected,
        lead1_output_pu=lead1,lead2_output_pu=lead2,regulator_output_pu=limited,current_limit_pu=current_limit,field_demand_pu=demand,
        field_floor_selected_pu=high,takeover_selected_pu=takeover,rectifier_lower_pu=lower,rectifier_upper_pu=upper,
        oel_active=float(high>ports['oel_ceiling_pu']),uel2_active=float(ports['uel_error_floor_pu']>clipped),uel3_active=float(ports['uel_field_floor_pu']>demand),
        rectifier_upper_active=float(takeover>upper),rectifier_lower_active=float(takeover<lower))


def rates(p,x,voltage,q):
    return np.array([(voltage-x[0])/p.T_R,(q['error_selected_pu']-x[1])/p.T_B,(q['lead1_output_pu']-x[2])/p.T_B1,
        (p.K_A*q['lead2_output_pu']-x[3])/p.T_A,(q['field_demand_pu']-x[4])/p.T_F])


class ESST1ABank:
    def __init__(self,model,offset):
        self.model=model;self.offset=offset;self.entries={};self.vref={};self.initial_vref={};self.reference_increment={};self.ports={};self.regions={};self.gates={}
        ids={g.id:i for i,g in enumerate(model.generators)}
        for p in model.settings.esst1a_exciters:
            i=ids[p.generator_id];self.entries[i]=(offset,p);offset+=5;self.regions[i]=0
            self.ports[i]={key:getattr(p,key,0.) for key in PORTS}
        self.end=offset

    def measurements(self,state,voltage=None,current=None):
        if not self.entries:return {}
        m=self.model
        if voltage is None:voltage=m.electrical(state)[0]
        if current is None:current=m.generator_currents(state,voltage)
        terminal=m.generator_terminal_voltage(state,voltage);rotor=state[m.gen_offset:m.rotor_end].reshape(-1,2)
        reaction=m.round_rotors.field_reaction(state,rotor,current)
        return {i:(float(abs(terminal[i])),float(reaction[i])) for i in self.entries}

    def values(self,state,voltage=None,current=None):
        uel=self.model.underexcitation_limiters.output(state);caps=self.model.takeover_oel.output(state)
        terminal=self.model.generator_terminal_voltage(state,self.model.electrical(state)[0] if voltage is None else voltage)
        rotor=state[self.model.gen_offset:self.model.rotor_end].reshape(-1,2)
        pss=self.model.controllers.pss.evaluate(state,rotor[:,1],terminal)[1]
        return {i:quantities(p,state[a:a+5],v,f,self.initial_vref[i],self.ports[i]|dict(
            oel_ceiling_pu=min(self.ports[i]['oel_ceiling_pu'],caps.get(i,float('inf'))),
            supplementary_voltage_pu=self.reference_increment[i]+(self.ports[i]['supplementary_voltage_pu']+pss[i])),uel[i])
            for i,(v,f) in self.measurements(state,voltage,current).items() for a,p in [self.entries[i]]}

    def initialize(self,state,voltage,current,field):
        eps=1e-8;measure=self.measurements(state,voltage,current);audit={}
        for i,(v,f) in measure.items():
            a,p=self.entries[i];va=field[i]+max(0.,p.K_LR*(f-p.I_LR));error=va/p.K_A
            if not p.V_AMIN+eps<va<p.V_AMAX-eps or not p.V_IMIN+eps<error<p.V_IMAX-eps:
                return dict(reason='initial_ESST1A_regulator_limit_or_boundary',generator=p.generator_id)
            if abs(f-p.I_LR)<=eps and p.K_LR>0:return dict(reason='initial_ESST1A_current_limit_boundary',generator=p.generator_id)
            if not (error>p.uel_error_floor_pu+eps and field[i]>p.uel_field_floor_pu+eps and field[i]<p.oel_ceiling_pu-eps):return dict(reason='initial_ESST1A_takeover_gate_or_boundary',generator=p.generator_id)
            if not v*p.V_RMIN+eps<field[i]<v*p.V_RMAX-p.K_C*f-eps:return dict(reason='initial_ESST1A_rectifier_limit_or_boundary',generator=p.generator_id)
            self.vref[i]=self.initial_vref[i]=float(v+error);self.reference_increment[i]=0.;state[a:a+5]=[v,error,error,va,field[i]]
            audit[p.generator_id]=dict(reference_pu=self.vref[i],regulator_initial_pu=float(va),field_initial_pu=float(field[i]),XADIFD_initial_pu=f,initial_gates='strictly_inactive')
        for i,q in self.values(state,voltage,current).items():self.gates[i]=self.gate_distances(i,q)
        self.initialization_audit=audit
        return None

    def reference_step(self,i,value):
        self.reference_increment[i]+=value;self.vref[i]=self.initial_vref[i]+self.reference_increment[i]

    def output(self,state,voltage=None,current=None):return {i:q['field_pu'] for i,q in self.values(state,voltage,current).items()}
    def derivatives(self,state,voltage,current):
        result=np.zeros(self.end-self.offset);measure=self.measurements(state,voltage,current)
        for i,q in self.values(state,voltage,current).items():
            a,p=self.entries[i];result[a-self.offset:a-self.offset+5]=rates(p,state[a:a+5],measure[i][0],q)
        return result

    def gate_distances(self,i,q):
        p=self.ports[i]
        cap=q.get('effective_oel_ceiling_pu',p['oel_ceiling_pu'])
        return dict(oel=q['field_floor_selected_pu']-cap,uel2=p['uel_error_floor_pu']-q['error_limited_pu'],uel3=p['uel_field_floor_pu']-q['field_demand_pu'],rectifier_upper=q['takeover_selected_pu']-q['rectifier_upper_pu'],rectifier_lower=q['rectifier_lower_pu']-q['takeover_selected_pu'])

    def free_rate(self,state,i):
        a,p=self.entries[i];return (p.K_A*self.values(state)[i]['lead2_output_pu']-state[a+3])/p.T_A

    def guards(self,state):
        ids=[];guards=[];eps=64*np.finfo(float).eps
        for i,(a,p) in self.entries.items():
            region=self.regions[i]
            if region:
                initial=self.free_rate(state,i);instant=initial>eps if region<0 else initial < -eps
                def reversal(t,x,i=i,region=region,instant=instant):return 0. if instant else self.free_rate(x,i)+(-eps if region<0 else eps)
                reversal.direction=1 if region<0 else -1;reversal.terminal=True;ids.append(('esst1a',i,'reset'));guards.append(reversal)
            else:
                for name,limit,direction in [('lower',p.V_AMIN,-1),('upper',p.V_AMAX,1)]:
                    def entry(t,x,a=a,limit=limit):return x[a+3]-limit
                    entry.direction=direction;entry.terminal=True;ids.append(('esst1a',i,name));guards.append(entry)
            for key,distance in self.gates.get(i,{}).items():
                active=distance>0
                def gate(t,x,i=i,key=key,active=active):return self.gate_distances(i,self.values(x)[i])[key]+(eps if active else -eps)
                gate.direction=-1 if active else 1;gate.terminal=True;ids.append(('esst1a',i,'gate_'+key));guards.append(gate)
        return ids,guards

    def apply(self,roots,time,state,log):
        for _,i,side in sorted(roots):
            if side.startswith('gate_'):continue
            a,p=self.entries[i];before=float(state[a+3]);region=self.regions[i]
            if side=='reset':
                state[a+3]=p.V_AMIN if region<0 else p.V_AMAX;rate=self.free_rate(state,i)
                self.regions[i]=(-1 if rate<=0 else 0) if region<0 else (1 if rate>=0 else 0)
            else:self.regions[i]=-1 if side=='lower' else 1
            log.append(dict(time_s=float(time),kind='esst1a_regulator_reset' if side=='reset' else 'esst1a_regulator_region_enter',equipment_id=p.generator_id,bound=('lower' if region<0 else 'upper') if side=='reset' else side,raw_state_before_pu=before,raw_state_after_pu=float(state[a+3])))
        self.refresh_gates(time,state,log)

    def refresh_gates(self,time,state,log):
        for i,q in self.values(state).items():
            previous=self.gates.get(i,{});now=self.gate_distances(i,q)
            for key,value in now.items():
                if (previous.get(key,0.)>0)!=(value>0):log.append(dict(time_s=float(time),kind='esst1a_gate_transition',equipment_id=self.entries[i][1].generator_id,gate=key,active=bool(value>0)))
            self.gates[i]=now

    def refresh(self,time,state,log):
        eps=64*np.finfo(float).eps;roots={('esst1a',i,'reset') for i in self.entries if self.regions[i] and (self.free_rate(state,i)>eps if self.regions[i]<0 else self.free_rate(state,i)<-eps)}
        self.apply(roots,time,state,log)

    def channel_names(self):return [p.generator_id+'.ESST1A.'+key for a,p in self.entries.values() for key in (*STATES,*VALUES,*PORTS)]
    def signals(self,state,voltage,current):
        values=self.values(state,voltage,current);measure=self.measurements(state,voltage,current);out={}
        for i,(a,p) in self.entries.items():
            row=dict(zip(STATES,state[a:a+5]))|values[i]|self.ports[i]|dict(reference_pu=self.vref[i],reference_increment_pu=self.reference_increment[i],field_current_pu=measure[i][1])
            out.update({p.generator_id+'.ESST1A.'+k:float(v) for k,v in row.items()})
        return out

    def validate(self,channels,rtol,atol):
        # Raw regulator memory may wind beyond its output limits by design.
        checks={}
        tolerance=max(1e-8,20*(rtol+atol))
        for a,p in self.entries.values():
            prefix=p.generator_id+'.ESST1A.'
            rows={k:np.asarray(channels[prefix+k],dtype=float) for k in (*STATES,*VALUES,*PORTS)}
            finite=all(np.isfinite(v).all() for v in rows.values())
            violations={
                'regulator_output':max(float(np.max(rows['regulator_output_pu']-p.V_AMAX)),float(np.max(p.V_AMIN-rows['regulator_output_pu'])),0.),
                'rectifier_output':max(float(np.max(rows['field_pu']-rows['rectifier_upper_pu'])),float(np.max(rows['rectifier_lower_pu']-rows['field_pu'])),0.),
                'rectifier_order':max(float(np.max(rows['rectifier_lower_pu']-rows['rectifier_upper_pu'])),0.),
            }
            checks[p.generator_id]=dict(finite=finite,maximum_violation_pu=max(violations.values()),violations_pu=violations)
        return dict(ok=all(v['finite'] and v['maximum_violation_pu']<=tolerance for v in checks.values()),limits=checks)
