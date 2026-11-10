"""Full-winding direct-on-line motor, relay and passive breaker commutation.

A three-pole contact has finite off resistance and an explicit parallel RC/MOV
snubber. Opening retains all winding currents, fluxes and capacitor voltages;
magnetic energy is dissipated physically, never discarded by a state reset.
The relay settings and snubber ratings are authored engineering inputs, not an
OEM breaker/relay certificate or an ideal galvanic-isolation model.
"""
import math
from typing import Literal
import numpy as np
from scipy.integrate import solve_ivp
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.phase_network import PhaseCase,PhaseNetwork,_abc,_space
from power_grid_env.runtime_limits import RuntimeLimits,IntegrationBudget,RuntimeLimitExceeded
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash


class ProtectionReset(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    time_s:float=Field(gt=0)


class MotorRelay(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id:str=Field(min_length=1)
    rated_current_rms_a:float=Field(gt=0)
    time_constant_s:float=Field(gt=0)
    trip_level:float=Field(gt=0)
    reset_level:float=Field(default=1.,ge=0)
    initial_level:float=Field(default=0.,ge=0)
    opening_delay_s:float=Field(default=.005,ge=0,le=1.)
    stator_trip_c:float|None=None
    provenance:str=Field(min_length=1)

    @model_validator(mode='after')
    def hysteresis(self):
        if not self.initial_level<self.trip_level or not self.reset_level<self.trip_level:raise ValueError('initial/reset relay levels must be below trip level')
        return self


class ProtectedMotorStudy(BaseModel):
    schema_version:Literal['power-grid-env.protected-motor-study/v1']='power-grid-env.protected-motor-study/v1'
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    case:PhaseCase
    contact_ids:tuple[str,str,str]
    relay:MotorRelay
    off_resistance_ohm:float=Field(default=1e8,gt=0)
    clamp_knee_v:float=Field(default=600.,gt=0)
    clamp_resistance_ohm:float=Field(default=2.,gt=0)
    reset_commands:list[ProtectionReset]=Field(default_factory=list,max_length=16)
    duration_s:float=Field(default=1.,gt=0,le=5.)
    sample_s:float=Field(default=.001,gt=0)
    max_step_s:float=Field(default=.00025,gt=0)
    rtol:float=Field(default=2e-8,gt=0,le=1e-5)
    atol:float=Field(default=2e-10,gt=0,le=1e-7)
    required_speed_pu:float=Field(default=.95,gt=0,lt=1.)
    maximum_runup_s:float=Field(default=.9,gt=0)
    supply_nodes:tuple[str,str,str]|None=None
    supply_available_pu:float=Field(default=.9,gt=0,le=1.)
    nominal_ln_rms_v:float=Field(default=400/math.sqrt(3),gt=0)
    minimum_closed_voltage_pu:float=Field(default=.8,gt=0,le=1.)
    runtime_limits:RuntimeLimits=Field(default_factory=RuntimeLimits)

    @model_validator(mode='after')
    def applicable(self):
        c=self.case
        if len(c.motors)!=1 or c.converters or c.drives or c.injections:raise ValueError('one direct Gamma motor, passive RLC and explicit voltage boundaries required')
        if len(set(self.contact_ids))!=3:raise ValueError('three distinct contact resistors required')
        contacts={r.id:r for r in c.resistors}
        if not set(self.contact_ids)<=contacts.keys():raise ValueError('unknown contact resistor')
        if len({contacts[k].resistance_ohm for k in self.contact_ids})!=1:raise ValueError('equal three-pole closed resistances required')
        if any(contacts[k].resistance_ohm>=self.off_resistance_ohm for k in self.contact_ids):raise ValueError('off resistance must exceed closed resistance')
        if any(e.kind!='resistance' for e in c.events):raise ValueError('only resistance commands supported in protected motor profile')
        if any(e.time_s>=self.duration_s for e in c.events) or any(e.time_s>=self.duration_s for e in self.reset_commands):raise ValueError('command outside protected study horizon')
        if len({e.time_s for e in self.reset_commands})!=len(self.reset_commands):raise ValueError('duplicate reset time')
        if any(t>self.duration_s for s in c.sources for t,_ in s.envelope):raise ValueError('source envelope outside protected horizon')
        if self.sample_s>self.duration_s or self.duration_s/self.sample_s>10000 or self.maximum_runup_s>self.duration_s:raise ValueError('protected study sampling/runup horizon')
        for t in {e.time_s for e in c.events}:
            commands=[e for e in c.events if e.time_s==t and e.equipment_id in self.contact_ids]
            if commands and any(e.value not in {contacts[self.contact_ids[0]].resistance_ohm,self.off_resistance_ohm} for e in commands):raise ValueError('contact command must select declared closed/off resistance')
            if commands and ({e.equipment_id for e in commands}!=set(self.contact_ids) or len({e.value for e in commands})!=1):raise ValueError('three-pole contact commands must act together with equal resistance')
        if self.supply_nodes is not None and (len(set(self.supply_nodes))!=3 or not set(self.supply_nodes)<=set(c.nodes)):raise ValueError('three distinct known supply-monitor nodes required')
        p=c.motors[0].passport
        if self.relay.stator_trip_c is not None and (p.thermal is None or not p.thermal.ambient_c<self.relay.stator_trip_c<p.thermal.maximum_c):raise ValueError('stator trip needs thermal passport and a threshold within its valid range')
        return self


class ProtectedCircuit(PhaseNetwork):
    def __init__(self,study):
        self.study=study;case=study.case.model_copy(deep=True);case.events=[]
        super().__init__(case)
        if self.size>64:raise ValueError('protected single-motor profile supports at most 64 physical states')
        contacts={r.id:r for r in self.case.resistors};self.contacts=[contacts[k] for k in study.contact_ids]
        self.contact_incidence=self._incidence(self.contacts)
        # Clamp voltages must be explicit dynamic or Dirichlet coordinates;
        # a nonlinear algebraic clamp needs a separate Newton formulation.
        if self.ua.shape[1] and np.max(abs(self.ua.T@self.contact_incidence[self.free]),initial=0.)>1e-10:
            raise ValueError('each contact needs explicit capacitive snubbing; nonlinear algebraic clamp unsupported')
        for r in self.contacts:
            if not any({c.frm,c.to}=={r.frm,r.to} for c in case.capacitors):raise ValueError('physical capacitor across every contact required')

    def clamp(self,t,x):
        v=np.zeros(self.n);v[self.known]=self.boundary(t);v[self.free]=self.ud@x[:self.nd]
        u=self.contact_incidence.T@v
        current=np.sign(u)*np.maximum(abs(u)-self.study.clamp_knee_v,0.)/self.study.clamp_resistance_ohm
        return u,current

    def injection(self,t,x):
        j,conv=super().injection(t,x)
        if hasattr(self,'contact_incidence'):
            _,current=self.clamp(t,x);j-=self.contact_incidence@current
        return j,conv

    def contacts_open(self):
        for r in self.contacts:r.resistance_ohm=self.study.off_resistance_ohm
        self.context(-1.)


def run_protected_motor(study):
    study=ProtectedMotorStudy.model_validate(study) if isinstance(study,dict) else study
    budget=IntegrationBudget(study.runtime_limits);code=package_fingerprint()
    try:model=ProtectedCircuit(study)
    except (ValueError,ArithmeticError) as exc:return dict(completed=False,status='rejected',reason=str(exc),code_sha256=code)
    motor=model.motors[study.case.motors[0].id];sl=model.slices[study.case.motors[0].id];p=motor.p
    # theta is a dimensionless external relay replica, excluded from energy.
    theta=model.size;entered=theta+1;loss=theta+2;arc=theta+3
    x=np.r_[model.initial(),study.relay.initial_level,0.,0.,0.]
    latched=False;opened=False;open_at=None;trip_time=None;acceleration=[];event_log=[];times=[];sides=[];channels={};energy=[];initial_energy=None;max_kcl=0.
    def quantities(t,state):
        dx,(v,i,j,info)=model.rhs(t,state[:model.size],True);values=info[study.case.motors[0].id]
        u,ic=model.clamp(t,state);clamp_loss=float(u@ic)
        dv=np.zeros(model.n);dv[model.known]=model.boundary(t,True);dv[model.free]=model.ud@dx[:model.nd]
        kcl=model.c@dv+model.g@v+model.a@i-j
        # MOV injection is an internal passive port; cancel it in entered work.
        pin=float(v[model.known]@kcl[model.known]+v@j+values['motor_power_w']+clamp_loss)
        dissipation=float(v@model.g@v+np.sum(model.r*i*i)+values['loss_w']+values['mechanical_power_w']+clamp_loss)
        rms=math.hypot(values['current_alpha_a'],values['current_beta_a'])/math.sqrt(2)
        heat=(rms/study.relay.rated_current_rms_a)**2
        derivative=np.r_[dx,(heat-state[theta])/study.relay.time_constant_s,pin,dissipation,clamp_loss]
        stored=float(.5*v@model.c@v+model.magnetic(i)+values['stored_j'])
        return derivative,v,i,info,u,ic,kcl,stored
    rhs=budget.rhs(lambda t,state:quantities(t,state)[0])
    def jacobian(t,state):
        from scipy.sparse import csc_matrix
        matrix=np.zeros((len(state),len(state)))
        for k in range(model.size):
            h=np.sqrt(np.finfo(float).eps)*max(1.,abs(state[k]));a=state.copy();b=state.copy();a[k]+=h;b[k]-=h
            matrix[:,k]=(rhs(t,a)-rhs(t,b))/(a[k]-b[k])
        matrix[theta,theta]=-1/study.relay.time_constant_s
        if not np.isfinite(matrix).all():raise ValueError('nonfinite protected motor Jacobian')
        return csc_matrix(matrix)
    def record(t,state,side):
        nonlocal initial_energy,max_kcl
        rate,v,i,info,u,ic,kcl,stored=quantities(t,state);values=info[study.case.motors[0].id]
        if initial_energy is None:initial_energy=stored
        residual=float(state[entered]-state[loss]-(stored-initial_energy))
        mismatch=float(np.max(abs(kcl[model.free]),initial=0.));max_kcl=max(max_kcl,mismatch)
        if not np.isfinite([stored,residual,mismatch]).all():raise ValueError('nonfinite protected motor balance')
        row={n+'.voltage_v':float(v[k]) for k,n in enumerate(model.case.nodes)}
        row.update({'motor.'+k:float(value) for k,value in values.items()})
        state_m=state[sl]
        temperature=state_m[motor.at+1:] if p.thermal else None;q=motor.quantities(state_m,_space(v[[model.index[n] for n in study.case.motors[0].nodes]]))
        row.update({'motor.stator_flux_alpha_vs':q['psi'].real,'motor.stator_flux_beta_vs':q['psi'].imag,'motor.stator_copper_w':q['stator_copper_w'],
                    'relay.level':state[theta],'relay.latched':float(latched),'breaker.opened':float(opened),'breaker.clamp_energy_j':state[arc]})
        for k,flux in enumerate(q['rotor_flux']):row['motor.cage'+str(k+1)+'_flux_alpha_vs']=flux.real;row['motor.cage'+str(k+1)+'_flux_beta_vs']=flux.imag
        if p.thermal:row.update({'motor.stator_temperature_c':temperature[0],'motor.rotor_temperature_c':temperature[1]})
        dv=np.zeros(model.n);dv[model.known]=model.boundary(t,True);dv[model.free]=model.ud@rate[:model.nd]
        du=model.contact_incidence.T@dv
        for k,id_ in enumerate(study.contact_ids):
            contact=model.contacts[k];capacitance=sum(c.capacitance_f for c in model.case.capacitors if {c.frm,c.to}=={contact.frm,contact.to})
            row[id_+'.ohmic_current_a']=float(u[k]/contact.resistance_ohm);row[id_+'.capacitor_current_a']=float(capacitance*du[k])
            row[id_+'.voltage_v']=float(u[k]);row[id_+'.clamp_current_a']=float(ic[k])
        if not np.isfinite(list(row.values())).all():raise ValueError('nonfinite protected output')
        budget.record(len(row)+len(state)+4);times.append(float(t));sides.append(side)
        for k,value in row.items():channels.setdefault(k,[]).append(float(value))
        energy.append(dict(stored_j=stored,entered_j=float(state[entered]),loss_j=float(state[loss]),residual_j=residual))
    scheduled={}
    for e in study.case.events:scheduled.setdefault(e.time_s,[]).append(e)
    resets={e.time_s for e in study.reset_commands}
    boundaries=sorted({study.duration_s,*scheduled,*resets,*[t for s in model.case.sources for t,_ in s.envelope if t>0]})
    grid=np.arange(math.ceil(study.duration_s/study.sample_s))*study.sample_s;cursor=0.
    try:
        record(0.,x,'initial')
        for boundary in boundaries:
            while cursor<boundary:
                target=min(boundary,cursor+min(.02,.5/max(1,model.size)))
                if open_at is not None:target=min(target,open_at)
                guards=[];guard_names=[]
                if not latched:
                    def thermal(t,state):return state[theta]-study.relay.trip_level
                    thermal.direction=1;thermal.terminal=True;guards.append(thermal);guard_names.append('thermal_i2t')
                    if study.relay.stator_trip_c is not None:
                        address=sl.start+motor.at+1
                        def winding(t,state):return state[address]-study.relay.stator_trip_c
                        winding.direction=1;winding.terminal=True;guards.append(winding);guard_names.append('stator_temperature')
                def speed(t,state):return state[sl.start+motor.at]-study.required_speed_pu*2*math.pi*model.case.frequency_hz/p.pole_pairs
                speed.direction=1;speed.terminal=False;guards.append(speed)
                sol=solve_ivp(rhs,(cursor,target),x,method='Radau',rtol=study.rtol,atol=study.atol,max_step=study.max_step_s,dense_output=True,events=guards,jac=jacobian)
                if not sol.success or not np.isfinite(sol.y).all():raise ValueError('protected motor integration failed')
                stop=float(sol.t[-1]);x=sol.y[:,-1].copy()
                epsilon=64*np.finfo(float).eps*max(1.,stop)
                if abs(stop-target)<=epsilon:stop=target
                for at in sol.t_events[-1]:
                    if not acceleration or at-acceleration[-1]>epsilon:acceleration.append(float(at))
                hit=[name for name,hits in zip(guard_names,sol.t_events[:-1]) if len(hits)]
                due=open_at is not None and abs(stop-open_at)<=epsilon
                commands=scheduled.get(boundary,[]) if stop==boundary else []
                reset=stop==boundary and boundary in resets
                discrete=bool(hit or due or commands or reset)
                sample=grid[(grid>cursor+epsilon)&(grid<stop-epsilon)].tolist()
                if discrete or stop==boundary or np.any(abs(grid-stop)<=epsilon):sample.append(stop)
                for t in sample:record(t,x if t==stop else sol.sol(t),'pre_event' if t==stop and discrete else 'regular')
                if discrete:
                    budget.event_batch()
                    if hit:
                        latched=True
                        if trip_time is None:trip_time=stop
                        open_at=stop+study.relay.opening_delay_s
                        event_log.append(dict(time_s=stop,kind='motor_relay_trip',equipment_id=study.relay.id,causes=sorted(hit),level=float(x[theta]),outcome='latched'))
                        due=study.relay.opening_delay_s==0
                    if due:
                        model.contacts_open();opened=True;open_at=None
                        event_log.append(dict(time_s=stop,kind='breaker_open',equipment_id=study.relay.id,outcome='applied'))
                    if reset:
                        permitted=open_at is None and x[theta]<=study.relay.reset_level and (not p.thermal or study.relay.stator_trip_c is None or x[sl.start+motor.at+1]<study.relay.stator_trip_c)
                        if permitted:latched=False
                        event_log.append(dict(time_s=stop,kind='relay_reset',equipment_id=study.relay.id,outcome='applied' if permitted else 'blocked_trip_pending' if open_at is not None else 'blocked_hot'))
                    for e in sorted(commands,key=lambda e:e.equipment_id):
                        closing=e.equipment_id in study.contact_ids and e.value<study.off_resistance_ohm
                        blocked=closing and (latched or open_at is not None)
                        if not blocked:
                            next(r for r in model.case.resistors if r.id==e.equipment_id).resistance_ohm=e.value
                            if closing:opened=False
                        event_log.append(dict(time_s=stop,kind=e.kind,equipment_id=e.equipment_id,value=e.value,outcome='blocked_thermal_latch' if blocked else 'applied'))
                    opened=all(r.resistance_ohm>=study.off_resistance_ohm for r in model.contacts)
                    model.context(-1.);record(stop,x,'post_event')
                if stop<=cursor and not discrete:raise ValueError('protected motor integrator made no progress')
                cursor=stop
        budget.check_time()
        maximum_energy=max(abs(e['residual_j'])/max(1.,abs(e['entered_j']),abs(e['stored_j'])) for e in energy)
        if max_kcl>1e-6 or maximum_energy>2e-5:raise ValueError('protected motor energy/KCL acceptance failed')
    except (ValueError,RuntimeError,ArithmeticError,RuntimeLimitExceeded) as exc:
        return dict(completed=False,status='runtime_limit_exceeded' if isinstance(exc,RuntimeLimitExceeded) else 'numerically_not_converged',reason=str(exc),event_log=event_log,runtime_budget=budget.report(),code_sha256=code)
    currents=np.hypot(channels['motor.current_alpha_a'],channels['motor.current_beta_a'])/math.sqrt(2)
    closed=[i for i,value in enumerate(channels['breaker.opened']) if not value]
    nodes=study.case.motors[0].nodes
    voltage=np.array([[_space(np.array([channels[n+'.voltage_v'][i] for n in nodes])) for i in range(len(times))]]).ravel()
    nominal=study.nominal_ln_rms_v
    min_voltage=float(min((abs(voltage[i])/math.sqrt(2)/nominal for i in closed),default=0.))
    available=closed
    if study.supply_nodes is not None:
        available=[i for i in closed if abs(_space(np.array([channels[n+'.voltage_v'][i] for n in study.supply_nodes])))/math.sqrt(2)/nominal>=study.supply_available_pu]
    available_min=float(min((abs(voltage[i])/math.sqrt(2)/nominal for i in available),default=0.))
    first=acceleration[0] if acceleration else None
    engineering=dict(classification='trip_pending' if open_at is not None else 'protected_trip' if latched else 'open' if opened else 'accelerated' if first is not None and first<=study.maximum_runup_s else 'not_accelerated',
        first_required_speed_time_s=first,runup_within_deadline=first is not None and first<=study.maximum_runup_s,
        required_speed_pu=study.required_speed_pu,maximum_runup_s=study.maximum_runup_s,
        sampled_peak_rms_equivalent_current_a=float(max(currents)),minimum_sampled_closed_voltage_pu=min_voltage,
        closed_voltage_limit_violated=min_voltage<study.minimum_closed_voltage_pu,minimum_sampled_available_voltage_pu=available_min,
        available_voltage_limit_violated=available_min<study.minimum_closed_voltage_pu,
        engineering_acceptable=bool(first is not None and first<=study.maximum_runup_s and not latched and not opened and open_at is None and available_min>=study.minimum_closed_voltage_pu),thermal_trip_time_s=trip_time,
        sampled_peak_contact_current_a=max(max(abs(v) for v in channels[k+'.ohmic_current_a']) for k in study.contact_ids),
        thermal_trip_count=sum(e['kind']=='motor_relay_trip' for e in event_log),
        final_relay_level=float(x[theta]),pending_open_time_s=open_at,clamp_energy_j=float(x[arc]),final_speed_pu=float(x[sl.start+motor.at]/(2*math.pi*model.case.frequency_hz/p.pole_pairs)),
        extrema_scope='exported samples; RMS-equivalent alpha-beta magnitude, not a certified cycle-RMS or continuous peak')
    return dict(completed=True,status='completed',code_sha256=code,study_sha256=canonical_hash(study),settings=study.model_dump(mode='json'),time_s=times,sample_side=sides,channels=channels,event_log=event_log,
        engineering=engineering,engineering_acceptable=engineering['engineering_acceptable'],energy=energy,maximum_kcl_a=max_kcl,maximum_energy_relative_residual=maximum_energy,runtime_budget=budget.report(),
        scope=dict(full_winding=True,breaker_model='finite off resistance with explicit capacitors and passive piecewise-ohmic clamps',flux_or_current_discarded=False,
                   calibrated_OEM_relay=False,ideal_galvanic_isolation=False,field_validated=False))
