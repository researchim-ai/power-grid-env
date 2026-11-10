"""Frozen pre-assembly-cache/SVD network formulation for before/after validation.

Only diagnostics counters are added to the former methods. Physical equations,
Newton/line search, trial coupling and constructor are retained verbatim.
"""
import math
import numpy as np
from power_grid_env.switching_drive import SwitchingDrive,_branch
from power_grid_env.rotor_flux_control import RotorFluxController
from power_grid_env.pwm_bridge import T,AB,thermal_step


class LegacyDrive(SwitchingDrive):
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
        self.reluctance=self.magnetics.linear_reluctance
        self.initial_energy=self.stored()
        self.linear_direct_solves=0;self.linear_least_squares_solves=0;self.linear_direct_fallbacks=0

    def source(self,t):
        p=self.p;envelope=np.array(p.grid_envelope);scale=np.interp(t,envelope[:,0],envelope[:,1])
        return math.sqrt(2)*p.grid_ln_rms_v*scale*np.cos(2*np.pi*p.grid_frequency_hz*t+np.array([0.,-2*np.pi/3,2*np.pi/3]))

    def _electrical(self,t,h,gates,wmid,winding_temperature=None):
        guess=self.flux.copy()
        for _ in range(10):
            port=self.magnetics.affine(self.flux,h,wmid,guess,winding_temperature)
            result=self._electrical_affine(t,h,gates,wmid,port,winding_temperature)
            if result['motor_constitutive_residual_v']<1e-8:return result
            guess=result['flux_mid']
        raise RuntimeError('nonlinear magnetic constitutive iteration did not converge')

    def _electrical_affine(self,t,h,gates,wmid,port,winding_temperature=None):
        p=self.p
        offset_flux=port['offset_flux'];gain_flux=port['gain_flux']
        motor_offset=port['motor_offset'];motor_gain=port['motor_gain']
        rpre=0. if self.bypassed else p.precharge_resistance_per_phase_ohm
        impedance=2*p.source_inductance_h/h+p.source_resistance_ohm+rpre
        incoming=(2*p.source_inductance_h/h*self.line+self.source(t+h/2))/impedance
        matrix=np.zeros((8,8));rhs=np.zeros(8);matrix[:3,:3]=np.eye(3)/impedance;rhs[:3]=incoming
        for a,c in zip(self.cap_branches,self.cap):
            matrix+=2*c/h*np.outer(a,a);rhs+=2*c/h*(a@self.voltage)*a
        dc=_branch(3,4);matrix+=np.outer(dc,dc)/p.bleed_ohm
        vdc=max(0.,self.voltage[3]-self.voltage[4])
        duty=float(np.clip((vdc-p.brake_start_v)/(p.brake_full_v-p.brake_start_v),0.,1.)) if not self.latched[2] else 0.
        matrix+=duty/p.brake_resistance_ohm*np.outer(dc,dc)
        matrix[5:,5:]+=T@motor_gain@AB;rhs[5:]-=T@motor_offset
        factors=1+p.thermal.resistance_alpha_per_k*(self.temperature-25.)
        if min(factors)<=0:raise ValueError('semiconductor resistance left positive thermal range')
        channel_r=p.mosfet_resistance_ohm*factors[1]
        for on,a in zip(np.r_[gates[:,0],gates[:,1]],self.channel_branches):
            if on:matrix+=np.outer(a,a)/channel_r
        branches=np.vstack([self.rect_branches,self.body_branches]);resistance=np.r_[np.full(6,p.rectifier_resistance_ohm*factors[0]),np.full(6,p.body_diode_resistance_ohm*factors[1])];drop=np.r_[np.full(6,p.rectifier_drop_v),np.full(6,p.body_diode_drop_v)]
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
            scaling=1/np.sqrt(np.maximum(np.abs(np.diag(jac)),1e-30))
            self.linear_least_squares_solves+=1
            correction=scaling*np.linalg.lstsq(scaling[:,None]*jac*scaling[None,:],-scaling*residual_v,rcond=1e-14)[0]
            fraction=1.
            base_forward=np.maximum(branches@voltage-drop,0.)
            gradient=(matrix+matrix.T)/2@voltage-rhs+branches.T@(base_forward/resistance)
            for _ in range(30):
                change=fraction*correction;trial=voltage+change
                forward=np.maximum(branches@trial-drop,0.)
                # Difference form avoids subtraction of the large capacitor
                # potential at a weakly conducting commutation boundary.
                potential_change=((matrix+matrix.T)/2@voltage-rhs)@change+.5*change@matrix@change+.5*sum((forward-base_forward)*(forward+base_forward)/resistance)
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
            external_w=float(p.source_resistance_ohm*(i_mid@i_mid)+(dc@voltage)**2/p.bleed_ohm),source_w=float(self.source(t+h/2)@i_mid),
            rectifier_conducting=int(sum(diode[:6]>1e-7)),body_diode_power_w=float(sum(diode_power[6:])),kcl_a=norm)

    def candidate(self,t,h,gates):
        p=self.p;wmid=self.speed;tmid=self.winding_temperature.copy()
        for _ in range(16):
            q=self._electrical(t,h,gates,wmid,tmid if p.motor.thermal else None)
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
