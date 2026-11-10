"""Event-resolved PWM drive with inductive diode commutation and thermal trips.

Bounded profile: saturated multicage isolated-star Gamma motor with core loss;
three source RL branches, six rectifier diodes, floating DC rails with declared
rail-to-earth capacitors, six MOSFET channels and antiparallel body diodes.
Radial discrete-gradient magnetics and midpoint storage preserve the energy law.
This is a piecewise-linear semiconductor model, not charge-dynamic device TCAD.
"""
import math
from typing import Literal
from pathlib import Path
import numpy as np
from pydantic import Field,model_validator,model_serializer
from power_grid_env.phase_network import Strict
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.rotor_flux_control import RotorFluxControl,RotorFluxController
from power_grid_env.pwm_bridge import T,AB,pwm_plan,gates_at,thermal_step
from power_grid_env.study_artifacts import encoded,seal
from power_grid_env.evidence import package_fingerprint


class PowerThermal(Strict):
    ambient_c: float = 25.
    initial_c: tuple[float,float,float] = (25.,25.,25.)
    capacity_j_per_k: tuple[float,float,float]
    conductance_w_per_k: tuple[float,float,float]
    trip_c: tuple[float,float,float]
    resistance_alpha_per_k: float = Field(default=.004,ge=0,le=.02)
    provenance: str = Field(min_length=1)

    @model_validator(mode='after')
    def valid(self):
        if not self.provenance.strip() or min(self.capacity_j_per_k)<=0 or min(self.conductance_w_per_k)<0 or any(t<=self.ambient_c for t in self.trip_c) or min(self.initial_c)<self.ambient_c or any(1+self.resistance_alpha_per_k*(t-25.)<=0 for t in self.initial_c):raise ValueError('positive thermal inventory, initial>=ambient, trip>ambient and provenance required')
        return self


class SwitchingDriveStudy(Strict):
    schema_version: str = 'power-grid-env.switching-drive/v1'
    id: str = Field(min_length=1)
    motor: FluxMachinePassport
    motor_initial_temperature_c: tuple[float,float] | None = None
    motor_temperature_trip_c: tuple[float,float] | None = None
    observer_motor: FluxMachinePassport | None = None
    observer_mode: Literal['nominal_single_cage','multicage_current'] = 'nominal_single_cage'
    motor_initial_fluxes_ab_vs: list[tuple[float,float]] | None = Field(default=None,min_length=2,max_length=5)
    control: RotorFluxControl
    thermal: PowerThermal
    duration_s: float = Field(gt=0,le=2)
    max_step_s: float = Field(ge=1e-9)
    sample_s: float = Field(ge=1e-9)
    pwm_frequency_hz: float = Field(gt=0,le=100_000)
    dead_time_s: float = Field(ge=1e-9)
    grid_ln_rms_v: float = Field(gt=0)
    grid_frequency_hz: float = Field(default=50.,gt=0)
    grid_envelope: list[tuple[float,float]] = Field(default_factory=lambda:[(0.,1.)])
    source_inductance_h: float = Field(gt=0)
    source_resistance_ohm: float = Field(gt=0)
    precharge_resistance_per_phase_ohm: float = Field(gt=0)
    precharge_ready_v: float = Field(gt=0)
    precharge_min_s: float = Field(default=0.,ge=0)
    dc_capacitance_f: float = Field(gt=0)
    rail_earth_capacitance_f: float = Field(gt=0)
    initial_dc_v: float = Field(default=0.,ge=0)
    bleed_ohm: float = Field(gt=0)
    rectifier_drop_v: float = Field(ge=0)
    rectifier_resistance_ohm: float = Field(gt=0)
    mosfet_resistance_ohm: float = Field(gt=0)
    body_diode_drop_v: float = Field(ge=0)
    body_diode_resistance_ohm: float = Field(gt=0)
    switching_on_j: float = Field(default=0.,ge=0)
    switching_off_j: float = Field(default=0.,ge=0)
    switching_reference_v: float = Field(gt=0)
    switching_reference_a: float = Field(gt=0)
    switching_temperature_per_k: float = Field(default=0.,ge=0,le=.02)
    enable_dc_v: float = Field(gt=0)
    disable_dc_v: float = Field(gt=0)
    brake_resistance_ohm: float = Field(gt=0)
    brake_start_v: float = Field(gt=0)
    brake_full_v: float = Field(gt=0)
    gate_block_windows: list[tuple[float,float]] = Field(default_factory=list)
    load_torque_nm: float = 0.
    provenance: dict[str,str]

    @model_serializer(mode='wrap')
    def canonical(self,handler):
        result=handler(self)
        for key in ['observer_motor','motor_initial_fluxes_ab_vs','motor_initial_temperature_c','motor_temperature_trip_c']:
            if getattr(self,key) is None:result.pop(key,None)
        if self.observer_mode=='nominal_single_cage':result.pop('observer_mode',None)
        return result

    @model_validator(mode='after')
    def profile(self):
        if self.schema_version!='power-grid-env.switching-drive/v1':raise ValueError('unsupported switching-drive schema')
        m=self.motor
        if m.locked_rotor:raise ValueError('switching profile requires a free shaft')
        if m.thermal is None:
            if self.motor_initial_temperature_c is not None or self.motor_temperature_trip_c is not None:raise ValueError('winding initial/trip temperatures require motor.thermal')
        else:
            h=m.thermal;initial=self.motor_initial_temperature_c or (h.ambient_c,h.ambient_c)
            if min(initial)<h.ambient_c or max(initial)>=h.maximum_c:raise ValueError('initial winding temperature must be >=ambient and below maximum_c')
            if self.motor_temperature_trip_c is not None and any(not h.ambient_c<t<h.maximum_c for t in self.motor_temperature_trip_c):raise ValueError('winding trip thresholds must lie between ambient and maximum_c')
            if max(self.max_step_s*(h.stator_ambient_w_per_k+h.cross_w_per_k)/h.stator_capacity_j_per_k,self.max_step_s*(h.rotor_ambient_w_per_k+h.cross_w_per_k)/h.rotor_capacity_j_per_k)>=2:raise ValueError('winding RC midpoint requires smaller max_step_s')
            if any(1+h.alpha_per_c*(t-h.reference_c)<=0 for t in initial):raise ValueError('initial winding resistance must be positive')
        if self.motor_initial_fluxes_ab_vs is not None and len(self.motor_initial_fluxes_ab_vs)!=1+len(m.cages):raise ValueError('initial flux inventory must include every winding')
        extended=len(m.cages)!=1 or bool(m.saturation_beta_per_vs) or bool(m.core_loss_s) or bool(m.thermal)
        observer=self.observer_motor
        if extended and observer is None:raise ValueError('extended plant requires an explicit nominal observer_motor; no silent first-cage approximation')
        if self.observer_mode=='multicage_current':
            if observer is None:raise ValueError('multicage_current requires explicit observer_motor')
            if observer.core_loss_s or observer.thermal or observer.locked_rotor or observer.pole_pairs!=m.pole_pairs:raise ValueError('multicage_current observer requires zero core loss, no thermal, free shaft and matching pole pairs')
        if self.observer_mode=='nominal_single_cage' and observer is not None and (len(observer.cages)!=1 or observer.saturation_beta_per_vs or observer.core_loss_s or observer.thermal or observer.locked_rotor or observer.pole_pairs!=m.pole_pairs):raise ValueError('observer_motor requires a linear single-cage zero-core-loss model with matching pole pairs')
        period=1/self.pwm_frequency_hz
        if 1+self.switching_temperature_per_k*(min(self.thermal.ambient_c,self.thermal.initial_c[1])-25.)<=0:raise ValueError('switching loss temperature factor must remain positive over the thermal range')
        if any(self.max_step_s*g/c>=2 for c,g in zip(self.thermal.capacity_j_per_k,self.thermal.conductance_w_per_k)):raise ValueError('thermal RC midpoint requires a smaller max_step_s for positive temperature relaxation')
        if self.dead_time_s>=period/10 or self.max_step_s>period/8 or self.duration_s/self.max_step_s>200_000 or self.duration_s*self.pwm_frequency_hz>20_000 or self.duration_s/self.sample_s>50_000:raise ValueError('bounded resolved PWM step/sample/carrier/dead-time profile required')
        if not self.disable_dc_v<self.enable_dc_v or not self.brake_start_v<self.brake_full_v:raise ValueError('ordered DC hysteresis and brake thresholds required')
        if not self.grid_envelope or self.grid_envelope[0][0]!=0 or any(t<0 or t>self.duration_s or v<0 for t,v in self.grid_envelope) or any(a[0]>=b[0] for a,b in zip(self.grid_envelope,self.grid_envelope[1:])):raise ValueError('ordered nonnegative grid envelope required')
        if any(not 0<=a<b<=self.duration_s for a,b in self.gate_block_windows):raise ValueError('bounded gate-block windows required')
        if not {'network','semiconductors','switching','thermal','control'}<=self.provenance.keys() or any(not x.strip() for x in self.provenance.values()):raise ValueError('complete physical parameter provenance required')
        return self


def _branch(a,b):
    v=np.zeros(8);v[a]=1.
    if b is not None:v[b]-=1.
    return v


class SwitchingDrive:
    def __init__(self,p):
        from power_grid_env.switching_magnetics import SwitchingMagnetics
        from power_grid_env.multicage_flux_control import MulticageFluxController
        controller=MulticageFluxController if p.observer_mode=='multicage_current' else RotorFluxController
        self.p=p;self.controller=controller(p.control,p.observer_motor or p.motor);self.magnetics=SwitchingMagnetics(p.motor)
        self.line=np.zeros(3);self.flux=np.zeros(self.magnetics.size);self.speed=p.motor.initial_speed_rad_s
        if p.motor_initial_fluxes_ab_vs is not None:self.flux=np.array(p.motor_initial_fluxes_ab_vs).ravel()
        self.voltage=np.zeros(8);self.voltage[3:5]=[p.initial_dc_v/2,-p.initial_dc_v/2]
        self.temperature=np.array(p.thermal.initial_c,dtype=float)
        self.latched=np.array(self.temperature>=np.array(p.thermal.trip_c));self.bypassed=False;self.enabled=False
        self.winding_temperature=np.array(p.motor_initial_temperature_c or [p.motor.thermal.ambient_c]*2,dtype=float) if p.motor.thermal else np.zeros(0)
        self.motor_latched=np.array(self.winding_temperature>=np.array(p.motor_temperature_trip_c)) if p.motor_temperature_trip_c else np.zeros(len(self.winding_temperature),dtype=bool)
        self.previous_gates=np.zeros((3,2),dtype=bool);self.events=[];self.work=0.;self.external_loss=0.;self.mechanical_work=0.;self.switch_heat=0.;self.steps=0;self.newton_calls=0;self.kcl_max=0.;self.magnetic_residual_max=0.;self.core_heat=0.;self.core_peak=0.;self.stator_heat=0.;self.rotor_heat=0.;self.winding_ambient_heat=0.
        for k in np.flatnonzero(self.motor_latched):self.events.append(dict(time_s=0.,kind='thermal_trip',component=['motor_stator','motor_rotor'][k],temperature_c=float(self.winding_temperature[k]),cause='initial_temperature'))
        self.cap=np.array([p.dc_capacitance_f,p.rail_earth_capacitance_f,p.rail_earth_capacitance_f])
        self.cap_branches=np.array([_branch(3,4),_branch(3,None),_branch(4,None)])
        self.rect_branches=np.array([_branch(i,3) for i in range(3)]+[_branch(4,i) for i in range(3)])
        self.body_branches=np.array([_branch(5+i,3) for i in range(3)]+[_branch(4,5+i) for i in range(3)])
        self.channel_branches=np.array([_branch(3,5+i) for i in range(3)]+[_branch(5+i,4) for i in range(3)])
        # Topological stamps only: material R/drop and all dynamic states are
        # still read afresh. No factorization or active-set state is cached.
        self.capacitance_stamp=(self.cap_branches.T*self.cap)@self.cap_branches
        self.dc_branch=_branch(3,4);self.dc_stamp=np.outer(self.dc_branch,self.dc_branch)
        self.diode_branches=np.vstack([self.rect_branches,self.body_branches])
        self.gate_stamps=np.empty((64,8,8))
        for code in range(64):
            self.gate_stamps[code]=sum((np.outer(a,a) for bit,a in enumerate(self.channel_branches) if code&(1<<bit)),np.zeros((8,8)))
        self.source_phases=np.array([0.,-2*np.pi/3,2*np.pi/3])
        self.linear_direct_solves=0;self.linear_least_squares_solves=0;self.linear_direct_fallbacks=0
        self.reluctance=self.magnetics.linear_reluctance
        self.initial_energy=self.stored()

    def trip_active(self):return bool(any(self.latched) or any(self.motor_latched))

    def source(self,t):
        p=self.p
        if len(p.grid_envelope)==1:scale=p.grid_envelope[0][1]
        else:
            envelope=np.asarray(p.grid_envelope);scale=np.interp(t,envelope[:,0],envelope[:,1])
        return math.sqrt(2)*p.grid_ln_rms_v*scale*np.cos(2*np.pi*p.grid_frequency_hz*t+self.source_phases)

    def _network_correction(self,jac,residual,fully_anchored):
        scaling=1/np.sqrt(np.maximum(np.abs(np.diag(jac)),1e-30))
        matrix=scaling[:,None]*jac*scaling[None,:];rhs=-scaling*residual
        if fully_anchored:
            try:
                solution=np.linalg.solve(matrix,rhs)
                if np.all(np.isfinite(solution)) and np.max(abs(matrix@solution-rhs))<=5e-12*(1+np.max(abs(rhs))):
                    self.linear_direct_solves+=1
                    return scaling*solution
            except np.linalg.LinAlgError:pass
            self.linear_direct_fallbacks+=1
        self.linear_least_squares_solves+=1
        return scaling*np.linalg.lstsq(matrix,rhs,rcond=1e-14)[0]

    def currents(self,flux=None):
        if flux is not None:return self.magnetics.magnetic_currents(flux)
        p=self.p;factor=1+p.thermal.resistance_alpha_per_k*(self.temperature[1]-25.)
        return self.magnetics.terminal_current(self.flux,self.voltage[3:5],self.previous_gates,p.mosfet_resistance_ohm*factor,p.body_diode_resistance_ohm*factor,p.body_diode_drop_v,self.winding_temperature if p.motor.thermal else None)

    def stored(self):
        p=self.p
        magnetic=self.magnetics.energy(self.flux)
        capacitor=.5*sum(self.cap*(self.cap_branches@self.voltage)**2)
        thermal=float(np.array(p.thermal.capacity_j_per_k)@(self.temperature-p.thermal.ambient_c))
        if p.motor.thermal:
            h=p.motor.thermal;thermal+=np.array([h.stator_capacity_j_per_k,h.rotor_capacity_j_per_k])@(self.winding_temperature-h.ambient_c)
        return float(magnetic+capacitor+.5*p.source_inductance_h*(self.line@self.line)+.5*p.motor.inertia_kg_m2*self.speed**2+thermal)

    def _network_context(self,t,h,gates):
        # One physical trial: only the motor port varies during the internal
        # shaft/thermal/magnetic iterations. Never reused across trial steps.
        p=self.p
        rpre=0. if self.bypassed else p.precharge_resistance_per_phase_ohm
        impedance=2*p.source_inductance_h/h+p.source_resistance_ohm+rpre
        source_mid=self.source(t+h/2)
        incoming=(2*p.source_inductance_h/h*self.line+source_mid)/impedance
        matrix=np.zeros((8,8));rhs=np.zeros(8);matrix[:3,:3]=np.eye(3)/impedance;rhs[:3]=incoming
        matrix+=2/h*self.capacitance_stamp;rhs+=2/h*(self.capacitance_stamp@self.voltage)
        dc=self.dc_branch;matrix+=self.dc_stamp/p.bleed_ohm
        vdc=max(0.,self.voltage[3]-self.voltage[4])
        duty=float(np.clip((vdc-p.brake_start_v)/(p.brake_full_v-p.brake_start_v),0.,1.)) if not self.latched[2] else 0.
        matrix+=duty/p.brake_resistance_ohm*self.dc_stamp
        factors=1+p.thermal.resistance_alpha_per_k*(self.temperature-25.)
        if min(factors)<=0:raise ValueError('semiconductor resistance left positive thermal range')
        channel_r=p.mosfet_resistance_ohm*factors[1]
        gate_code=sum(1<<(phase+3*side) for side in range(2) for phase in range(3) if gates[phase,side])
        matrix+=self.gate_stamps[gate_code]/channel_r
        branches=self.diode_branches
        resistance=np.empty(12);resistance[:6]=p.rectifier_resistance_ohm*factors[0];resistance[6:]=p.body_diode_resistance_ohm*factors[1]
        drop=np.empty(12);drop[:6]=p.rectifier_drop_v;drop[6:]=p.body_diode_drop_v
        return dict(matrix=matrix,rhs=rhs,incoming=incoming,impedance=impedance,source_mid=source_mid,dc=dc,duty=duty,rpre=rpre,channel_r=channel_r,branches=branches,resistance=resistance,drop=drop)

    def _electrical(self,t,h,gates,wmid,winding_temperature=None,context=None):
        context=self._network_context(t,h,gates) if context is None else context
        guess=self.flux.copy()
        for _ in range(10):
            port=self.magnetics.affine(self.flux,h,wmid,guess,winding_temperature)
            result=self._electrical_affine(t,h,gates,wmid,port,winding_temperature,context)
            if result['motor_constitutive_residual_v']<1e-8:return result
            guess=result['flux_mid']
        raise RuntimeError('nonlinear magnetic constitutive iteration did not converge')

    def _electrical_affine(self,t,h,gates,wmid,port,winding_temperature=None,context=None):
        p=self.p
        offset_flux=port['offset_flux'];gain_flux=port['gain_flux']
        motor_offset=port['motor_offset'];motor_gain=port['motor_gain']
        context=self._network_context(t,h,gates) if context is None else context
        matrix=context['matrix'].copy();rhs=context['rhs'].copy()
        matrix[5:,5:]+=T@motor_gain@AB;rhs[5:]-=T@motor_offset
        incoming=context['incoming'];impedance=context['impedance'];source_mid=context['source_mid']
        dc=context['dc'];duty=context['duty'];rpre=context['rpre'];channel_r=context['channel_r']
        branches=context['branches'];resistance=context['resistance'];drop=context['drop']
        symmetric=(matrix+matrix.T)/2
        def residual(v):
            diode=np.maximum(0.,(branches@v-drop)/resistance)
            return matrix@v-rhs+branches.T@diode,diode
        voltage=self.voltage.copy()
        for iteration in range(45):
            self.newton_calls+=1
            residual_v,diode=residual(voltage);norm=float(np.max(abs(residual_v)))
            if norm<max(1e-8,8*np.finfo(float).eps*np.max(abs(matrix))*max(1.,max(abs(voltage)))):break
            active=(branches@voltage)>drop
            jac=matrix+(branches[active].T/resistance[active])@branches[active]
            # With all motor gates/diodes off the isolated-star common mode is
            # physically undetermined. Least squares selects a gauge only;
            # no resistor or capacitor is inserted into that isolated motor.
            anchored=np.any(gates,axis=1)|active[6:9]|active[9:12]
            correction=self._network_correction(jac,residual_v,bool(np.all(anchored)))
            fraction=1.
            base_forward=np.maximum(branches@voltage-drop,0.)
            smooth_gradient=symmetric@voltage-rhs
            gradient=smooth_gradient+branches.T@(base_forward/resistance)
            for _ in range(30):
                change=fraction*correction;trial=voltage+change
                forward=np.maximum(branches@trial-drop,0.)
                # Difference form avoids subtraction of the large capacitor
                # potential at a weakly conducting commutation boundary.
                potential_change=smooth_gradient@change+.5*change@matrix@change+.5*sum((forward-base_forward)*(forward+base_forward)/resistance)
                if max(abs(residual(trial)[0]))<norm or potential_change<min(-1e-14,1e-4*gradient@change):
                    voltage=trial;break
                fraction*=.5
            else:raise RuntimeError(f'switching circuit Newton line search failed at t={t}, h={h}, residual={norm}')
        else:raise RuntimeError('switching circuit Newton failed to converge')
        i_mid=incoming-voltage[:3]/impedance
        increment=port['offset_delta']+gain_flux@(AB@voltage[5:]);phi_mid=self.flux+increment;machine=self.magnetics.stage(self.flux,phi_mid,h,AB@voltage[5:],increment,winding_temperature);currents=machine['currents']
        torque=machine['torque_nm']
        # Gate-on channel power and body-diode power are both real branches.
        channel_power=sum((a@voltage)**2/channel_r for on,a in zip(np.r_[gates[:,0],gates[:,1]],self.channel_branches) if on)
        diode_power=diode*(branches@voltage)
        bridge_heat=float(sum(diode_power[:6])+rpre*(i_mid@i_mid))
        inverter_heat=float(sum(diode_power[6:])+channel_power)
        brake_heat=float(duty*(dc@voltage)**2/p.brake_resistance_ohm)
        physical_residual=residual(voltage)[0];physical_residual[5:]+=T@(currents[:2]-(motor_offset+motor_gain@(AB@voltage[5:])))
        norm=float(max(abs(physical_residual)))
        load=p.load_torque_nm+p.motor.fan_nm_per_rad_s2*wmid*abs(wmid)+p.motor.viscous_nm_per_rad_s*wmid
        return dict(voltage=voltage,line_mid=i_mid,flux_mid=phi_mid,flux_increment_mid=increment,currents_mid=currents,torque_nm=float(torque),load_nm=float(load),
            heat=np.array([bridge_heat,inverter_heat,brake_heat]),motor_copper_w=machine['copper_w']+machine['core_w'],motor_stator_copper_w=machine['stator_copper_w'],motor_rotor_copper_w=machine['rotor_copper_w'],motor_stator_resistance_ohm=machine['stator_resistance_ohm'],motor_rotor_resistances_ohm=machine['rotor_resistances_ohm'],motor_core_loss_w=machine['core_w'],motor_constitutive_residual_v=machine['constitutive_residual_v'],
            external_w=float(p.source_resistance_ohm*(i_mid@i_mid)+(dc@voltage)**2/p.bleed_ohm),source_w=float(source_mid@i_mid),
            rectifier_conducting=int(sum(diode[:6]>1e-7)),body_diode_power_w=float(sum(diode_power[6:])),kcl_a=norm)

    def candidate(self,t,h,gates):
        p=self.p;wmid=self.speed;tmid=self.winding_temperature.copy()
        context=self._network_context(t,h,gates)
        for _ in range(16):
            q=self._electrical(t,h,gates,wmid,tmid if p.motor.thermal else None,context)
            next_mid=self.speed+h/(2*p.motor.inertia_kg_m2)*(q['torque_nm']-q['load_nm'])
            temperature_error=0.
            if p.motor.thermal:
                from power_grid_env.switching_winding_thermal import winding_thermal_step
                q['winding_temperature'],q['winding_ambient_j']=winding_thermal_step(p.motor.thermal,self.winding_temperature,[q['motor_stator_copper_w']+q['motor_core_loss_w'],q['motor_rotor_copper_w']],h)
                next_tmid=(self.winding_temperature+q['winding_temperature'])/2
                temperature_error=max(abs(next_tmid-tmid));tmid=next_tmid
            if abs(next_mid-wmid)<1e-10 and temperature_error<1e-9:
                wmid=next_mid;break
            wmid=next_mid
        else:raise RuntimeError('electrothermal/mechanical midpoint iteration failed')
        q['speed_mid']=wmid
        q['temperature'],q['ambient_j']=thermal_step(self.temperature,q['heat'],h,np.array(p.thermal.capacity_j_per_k),np.array(p.thermal.conductance_w_per_k),p.thermal.ambient_c)
        return q

    def _thermal_crossing(self,q):
        electronics=(~self.latched)&(q['temperature']>=np.array(self.p.thermal.trip_c))&(self.temperature<np.array(self.p.thermal.trip_c))
        if any(electronics):return True
        if self.p.motor_temperature_trip_c:
            limit=np.array(self.p.motor_temperature_trip_c)
            return bool(any((~self.motor_latched)&(q['winding_temperature']>=limit)&(self.winding_temperature<limit)))
        return False

    def advance(self,t,h,gates):
        p=self.p;q=self.candidate(t,h,gates)
        if self._thermal_crossing(q):
            # Locate the first threshold before accepting a step, then retry
            # from the same state. Neither trials nor Newton mutate physics.
            low=0.;high=h
            for _ in range(30):
                mid=(low+high)/2;trial=self.candidate(t,mid,gates)
                if self._thermal_crossing(trial):high=mid
                else:low=mid
                if high-low<1e-11:break
            h=high;q=self.candidate(t,h,gates)
        if 2*(q['voltage'][3]-q['voltage'][4])-(self.voltage[3]-self.voltage[4]) < -1e-6:raise ValueError('negative DC voltage after midpoint step; refine the switching step')
        if p.motor.thermal and max(q['winding_temperature'])>=p.motor.thermal.maximum_c:raise ValueError('winding temperature exceeded declared maximum_c; trip does not remove stored magnetic energy')
        self.line=2*q['line_mid']-self.line;self.flux=self.flux+2*q['flux_increment_mid'];self.speed=2*q['speed_mid']-self.speed
        # Only capacitor voltages are dynamic. Motor/source terminal potentials
        # are algebraic midpoint quantities and must not be extrapolated.
        self.voltage=q['voltage'].copy()
        self.voltage[3:5]=2*q['voltage'][3:5]-self._old_rails
        if p.motor.thermal:
            self.winding_temperature=q['winding_temperature']
        self.temperature=q['temperature'];self.work+=h*q['source_w'];self.external_loss+=h*q['external_w']+(sum(q['winding_ambient_j']) if p.motor.thermal else h*q['motor_copper_w'])+sum(q['ambient_j']);self.mechanical_work+=h*q['load_nm']*q['speed_mid'];self.steps+=1;self.kcl_max=max(self.kcl_max,q['kcl_a']);self.magnetic_residual_max=max(self.magnetic_residual_max,q['motor_constitutive_residual_v']);self.core_heat+=h*q['motor_core_loss_w'];self.core_peak=max(self.core_peak,q['motor_core_loss_w']);self.stator_heat+=h*(q['motor_stator_copper_w']+q['motor_core_loss_w']);self.rotor_heat+=h*q['motor_rotor_copper_w'];self.winding_ambient_heat+=sum(q['winding_ambient_j']) if p.motor.thermal else h*q['motor_copper_w']
        newly=(~self.latched)&(self.temperature>=np.array(p.thermal.trip_c)-1e-8)
        for index in np.flatnonzero(newly):self.events.append(dict(time_s=t+h,kind='thermal_trip',component=['rectifier','inverter','brake'][index],temperature_c=float(self.temperature[index])))
        self.latched|=newly
        if p.motor_temperature_trip_c:
            newly_motor=(~self.motor_latched)&(self.winding_temperature>=np.array(p.motor_temperature_trip_c)-1e-8)
            for k in np.flatnonzero(newly_motor):self.events.append(dict(time_s=t+h,kind='thermal_trip',component=['motor_stator','motor_rotor'][k],temperature_c=float(self.winding_temperature[k])))
            self.motor_latched|=newly_motor
        if self.steps>300_000:raise RuntimeError('switching-step budget exceeded')
        return h,q

    def switch(self,t,gates):
        if np.any(gates[:,0]&gates[:,1]):raise ValueError('half-bridge shoot-through command rejected')
        p=self.p;changed=gates!=self.previous_gates
        if not np.any(changed):return 0
        current=T@self.currents()[:2];vdc=max(0.,self.voltage[3]-self.voltage[4])
        factor=1+p.switching_temperature_per_k*(self.temperature[1]-25.)
        resistance_factor=1+p.thermal.resistance_alpha_per_k*(self.temperature[1]-25.)
        after=T@self.magnetics.terminal_current(self.flux,self.voltage[3:5],gates,p.mosfet_resistance_ohm*resistance_factor,p.body_diode_resistance_ohm*resistance_factor,p.body_diode_drop_v,self.winding_temperature if p.motor.thermal else None)[:2]
        energy=sum((p.switching_on_j*abs(after[i]) if gates[i,k] else p.switching_off_j*abs(current[i]))/p.switching_reference_a*vdc/p.switching_reference_v*factor for i,k in zip(*np.nonzero(changed)))
        if energy:
            effective=p.dc_capacitance_f+p.rail_earth_capacitance_f/2
            remaining=vdc*vdc-2*energy/effective
            if remaining<0:raise ValueError('switching loss exceeds available DC energy')
            center=.5*(self.voltage[3]+self.voltage[4]);voltage=math.sqrt(remaining)
            self.voltage[3:5]=[center+voltage/2,center-voltage/2];self.temperature[1]+=energy/p.thermal.capacity_j_per_k[1];self.switch_heat+=energy
        self.previous_gates=gates.copy()
        newly=(~self.latched)&(self.temperature>=np.array(p.thermal.trip_c))
        for index in np.flatnonzero(newly):self.events.append(dict(time_s=t,kind='thermal_trip',component=['rectifier','inverter','brake'][index],temperature_c=float(self.temperature[index]),cause='switching_energy'))
        self.latched|=newly
        return int(sum(changed.ravel()))

    def step(self,t,h,gates):
        self._old_rails=self.voltage[3:5].copy()
        return self.advance(t,h,gates)


def run_switching_drive(study):
    p=SwitchingDriveStudy.model_validate(study);m=SwitchingDrive(p);initial_motor_flags=[bool(x) for x in m.motor_latched];period=1/p.pwm_frequency_hz
    times=[];channels={};energy=[];last_control={};last_q={};edge_count=0;next_sample=0.;t=0.;peak_current=0.;overlap_steps=0;body_work=0.;clock_tolerance=5e-13
    def record():
        currents=m.currents();vdc=m.voltage[3]-m.voltage[4]
        row=dict(dc_voltage_v=float(vdc),speed_rad_s=float(m.speed),motor_current_alpha_a=float(currents[0]),motor_current_beta_a=float(currents[1]),motor_current_peak_a=float(np.linalg.norm(currents[:2])),source_current_a=float(m.line[0]),source_current_b=float(m.line[1]),source_current_c=float(m.line[2]),rectifier_temperature_c=float(m.temperature[0]),inverter_temperature_c=float(m.temperature[1]),brake_temperature_c=float(m.temperature[2]),thermal_trip=bool(m.trip_active()),gates_enabled=bool(m.enabled and not m.trip_active() and not any(a<=t<b for a,b in p.gate_block_windows)),precharge_bypassed=bool(m.bypassed),**last_control)
        if p.motor.thermal:
            rs,rr=m.magnetics.resistances(m.winding_temperature)
            row.update(motor_stator_temperature_c=float(m.winding_temperature[0]),motor_rotor_temperature_c=float(m.winding_temperature[1]),motor_stator_resistance_ohm=float(rs),motor_stator_thermal_trip=bool(m.motor_latched[0]),motor_rotor_thermal_trip=bool(m.motor_latched[1]))
            for k,r in enumerate(rr,1):row[f'rotor_cage_{k}_resistance_ohm']=float(r)
        for cage in range(1,m.magnetics.n):
            row.update({f'rotor_cage_{cage}_flux_alpha_vs':float(m.flux[2*cage]),f'rotor_cage_{cage}_flux_beta_vs':float(m.flux[2*cage+1])})
        row.update(motor_stator_flux_alpha_vs=float(m.flux[0]),motor_stator_flux_beta_vs=float(m.flux[1]),rotor_flux_alpha_vs=float(m.flux[2]),rotor_flux_beta_vs=float(m.flux[3]))
        if last_q:
            last_voltage=AB@last_q['voltage'][5:]
            row.update(last_midpoint_inverter_voltage_alpha_v=float(last_voltage[0]),last_midpoint_inverter_voltage_beta_v=float(last_voltage[1]),last_midpoint_torque_nm=last_q['torque_nm'],last_midpoint_rectifier_conducting=last_q['rectifier_conducting'],last_midpoint_body_diode_power_w=last_q['body_diode_power_w'],last_midpoint_motor_core_loss_w=last_q['motor_core_loss_w'],last_midpoint_motor_constitutive_residual_v=last_q['motor_constitutive_residual_v'])
        for key,value in row.items():channels.setdefault(key,[]).append(float(value))
        times.append(float(t));stored=m.stored();energy.append(dict(stored_j=stored,source_j=float(m.work),external_heat_j=float(m.external_loss),mechanical_j=float(m.mechanical_work),residual_j=float(m.work-m.external_loss-m.mechanical_work-(stored-m.initial_energy))))
    record()
    periods=math.ceil(p.duration_s/period)
    for n in range(periods):
        start=n*period;end=min((n+1)*period,p.duration_s)
        if end<=start+1e-15:break
        vdc=m.voltage[3]-m.voltage[4]
        if not m.trip_active() and not m.bypassed and start>=p.precharge_min_s and vdc>=p.precharge_ready_v:
            m.bypassed=True;m.events.append(dict(time_s=start,kind='precharge_bypass',dc_voltage_v=float(vdc)))
        m.enabled=bool(not m.trip_active() and m.bypassed and vdc>=(p.disable_dc_v if m.enabled else p.enable_dc_v))
        current=complex(*m.currents()[:2]);control_enabled=m.enabled and not any(a<=start<b for a,b in p.gate_block_windows)
        u,last_control=m.controller.update(start,current,m.speed,max(0.,vdc),period,control_enabled)
        duty,edges=pwm_plan(u,max(0.,vdc),period,p.dead_time_s)
        boundaries={start+e for e in edges if start+e<end-1e-14};boundaries.add(end)
        boundaries.update(x for a,b in p.gate_block_windows for x in [a,b] if start<x<end)
        boundaries.update(x for x,_ in p.grid_envelope if start<x<end)
        current_integral=0j;speed_integral=0.;elapsed=0.
        for boundary in sorted(boundaries):
            if boundary<=t+clock_tolerance:
                t=boundary;continue
            while t<boundary-clock_tolerance:
                blocked=any(a<=t+1e-13<b for a,b in p.gate_block_windows)
                gates=gates_at((t-start)+1e-13,duty,period,p.dead_time_s,m.enabled and not m.trip_active() and not blocked)
                edge_count+=m.switch(t,gates)
                if m.trip_active() and np.any(gates):
                    gates=np.zeros((3,2),dtype=bool);edge_count+=m.switch(t,gates)
                step=min(p.max_step_s,boundary-t)
                if next_sample<=t+clock_tolerance:next_sample+=p.sample_s
                step=min(step,next_sample-t)
                h,q=m.step(t,step,gates);t+=h;last_q=q
                current_integral+=h*complex(*q['currents_mid'][:2]);speed_integral+=h*q['speed_mid'];elapsed+=h
                peak_current=max(peak_current,float(np.linalg.norm(m.currents()[:2])));overlap_steps+=int(q['rectifier_conducting']>2);body_work+=h*q['body_diode_power_w']
                if abs(t-next_sample)<clock_tolerance or t>=p.duration_s-clock_tolerance:record()
            # Coincident carrier/sample boundaries can differ by floating-point
            # roundoff. Snap only sub-picosecond gaps, never a physical pulse.
            t=boundary
            if abs(t-next_sample)<clock_tolerance and t>times[-1]+clock_tolerance:record()
        if elapsed:m.controller.observe(current_integral/elapsed,speed_integral/elapsed,elapsed)
    # Controller channels have no initial output; fill a declared zero initial
    # snapshot rather than silently exporting unequal channel inventories.
    for key,values in channels.items():
        if len(values)==len(times)-1:values.insert(0,0.)
        if len(values)!=len(times):raise RuntimeError('switching output inventory differs')
    normalizer=max(1.,abs(m.work),abs(m.external_loss),abs(m.mechanical_work),m.initial_energy)
    return dict(completed=True,time_s=times,channels=channels,energy=energy,event_log=m.events,
        metrics=dict(final_energy_relative_error=abs(energy[-1]['residual_j'])/normalizer,kcl_max_a=m.kcl_max,motor_constitutive_residual_v=m.magnetic_residual_max,motor_core_heat_j=float(m.core_heat),motor_stator_heat_j=float(m.stator_heat),motor_rotor_heat_j=float(m.rotor_heat),motor_winding_ambient_heat_j=float(m.winding_ambient_heat),peak_substep_motor_core_loss_w=float(m.core_peak),peak_endpoint_motor_current_a=peak_current,inductive_overlap_steps=overlap_steps,body_diode_heat_j=float(body_work),switching_heat_j=float(m.switch_heat),gate_edges=edge_count,steps=m.steps,newton_iterations=m.newton_calls,linear_direct_solves=m.linear_direct_solves,linear_least_squares_solves=m.linear_least_squares_solves,linear_direct_fallbacks=m.linear_direct_fallbacks),
        observer_mode=p.observer_mode,
        model_scope='1-4 cage saturated Gamma machine with eddy-current core loss; explicit nominal single-cage or saturated multicage current-model sensored FOC observer; event-resolved PWM MOSFET/body diodes; source RL diode commutation; explicit rail-earth C; three electronics thermal states and optional two winding RC/R(T) states; no diode charge dynamics, avalanche or EMI qualification',
        initial_winding_thermal_trips=initial_motor_flags,initial_thermal_trips=[bool(x) for x in np.array(p.thermal.initial_c)>=np.array(p.thermal.trip_c)],
        coincident_boundary_tolerance_s=clock_tolerance,extrema_scope='accepted substep endpoints for current, exported samples for channels; not certified continuous extrema')


def run_switching_artifact(document,out):
    p=SwitchingDriveStudy.model_validate(document);producer=package_fingerprint();result=run_switching_drive(p.model_dump(mode='json'))
    if producer!=package_fingerprint():raise RuntimeError('switching producer changed during run')
    result['code_sha256']=producer;out=Path(out);out.mkdir(parents=True,exist_ok=False)
    (out/'study.json').write_bytes(encoded(p.model_dump(mode='json')));(out/'result.json').write_bytes(encoded(result));seal(out)
    return result
