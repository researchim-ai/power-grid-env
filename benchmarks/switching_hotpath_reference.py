"""Frozen R06.13 numerical hot paths for before/after qualification.

Original package SHA256: 7e07118eb961451d95a98e999d7241e80409a550d68ffe2a69de098e8f357a33.
This is the previous implementation, not an independent physical oracle.
"""
import math
import numpy as np
from power_grid_env.switching_magnetics import SwitchingMagnetics
from power_grid_env.switching_drive import SwitchingDrive,_branch
from power_grid_env.rotor_flux_control import RotorFluxController
from power_grid_env.pwm_bridge import T,AB,thermal_step


class LegacyMagnetics(SwitchingMagnetics):
    def radial_gradient(self,old,mid):
        p=self.p;new=2*mid-old;a=math.hypot(old[0],old[1]);b=math.hypot(new[0],new[1]);power=p.saturation_exponent+2
        coefficient=.5/p.unsaturated_h;gradient_coefficient=np.zeros(2)
        if p.saturation_beta_per_vs and a+b>0:
            # (b**power-a**power)/(b*b-a*a), with its equal-radius limit.
            total=sum(b**(power-1-k)*a**k for k in range(power))
            derivative=sum((power-1-k)*b**(power-2-k)*a**k for k in range(power-1))
            scale=p.saturation_beta_per_vs**p.saturation_exponent/(power*p.unsaturated_h)
            coefficient+=scale*total/(a+b)
            if b>0:gradient_coefficient=scale*(derivative*(a+b)-total)/(a+b)**2*(2*new/b)
        value=2*mid*coefficient
        jac=2*coefficient*self.eye2+2*np.outer(mid,gradient_coefficient)
        return value,jac

    def magnetic_currents(self,flux):
        p=self.p;z=np.asarray(flux).reshape(self.n,2);ir=(z[1:]-z[0])/self.leakage[:,None]
        force=z[0]*(1+(p.saturation_beta_per_vs*np.linalg.norm(z[0]))**p.saturation_exponent)/p.unsaturated_h-sum(ir)
        return np.r_[force,ir.ravel()]

    def affine(self,old,h,speed,guess,temperature=None):
        p=self.p;rs,rr=self.resistances(temperature);matrix=self.linear_reluctance.copy();gradient,jac=self.radial_gradient(old[:2],guess[:2]);matrix[:2,:2]+=jac-self.eye2/p.unsaturated_h
        offset=np.zeros(self.size);offset[:2]=gradient-jac@guess[:2]
        # Current-coordinate gradient rows, plus explicit core feedthrough
        # expressed in flux increments. This yields a Norton voltage port.
        physical=matrix.copy();physical[:2,:2]+=p.core_loss_s*2/h*self.eye2
        physical_offset=offset.copy();physical_offset[:2]-=p.core_loss_s*2/h*old[:2]
        resistance=np.repeat(np.r_[rs,rr],2)
        left=2/h*self.identity+resistance[:,None]*physical
        spin=self.spin_unit*p.pole_pairs*speed
        for k in range(1,self.n):left[2*k:2*k+2,2*k:2*k+2]-=spin
        rhs=-resistance*(matrix@old+offset)
        for k in range(1,self.n):rhs[2*k:2*k+2]+=spin@old[2*k:2*k+2]
        B=self.input_matrix
        solved=np.linalg.solve(left,np.column_stack([rhs,B]));offset_delta=solved[:,0];offset_flux=old+offset_delta;gain_flux=solved[:,1:]
        motor_offset=(matrix@old+offset)[:2]+physical[:2]@offset_delta;motor_gain=physical[:2]@gain_flux
        return dict(offset_delta=offset_delta,offset_flux=offset_flux,gain_flux=gain_flux,motor_offset=motor_offset,motor_gain=motor_gain,
                    physical_matrix=physical,physical_offset=physical_offset,gradient=gradient,jacobian=jac,guess=guess.copy())

    def stage(self,old,mid,h,voltage,increment=None,temperature=None):
        p=self.p;rs,rr=self.resistances(temperature);z=np.asarray(mid).reshape(self.n,2);ir=(z[1:]-z[0])/self.leakage[:,None]
        gradient,_=self.radial_gradient(old[:2],mid[:2]);force=gradient-sum(ir);ds=2/h*((mid[:2]-old[:2]) if increment is None else increment[:2]);current=force+p.core_loss_s*ds
        expected=ds+rs*current
        torque=1.5*p.pole_pairs*(force[1]*z[0,0]-force[0]*z[0,1])
        stator_copper=1.5*rs*(current@current);rotor_copper=1.5*sum(rr*np.sum(ir*ir,axis=1));copper=stator_copper+rotor_copper
        core=1.5*p.core_loss_s*(ds@ds)
        return dict(currents=np.r_[current,ir.ravel()],torque_nm=float(torque),copper_w=float(copper),stator_copper_w=float(stator_copper),rotor_copper_w=float(rotor_copper),stator_resistance_ohm=float(rs),rotor_resistances_ohm=rr.tolist(),core_w=float(core),constitutive_residual_v=float(max(abs(expected-voltage))))


class LegacyDrive(SwitchingDrive):
    def __init__(self,p):
        SwitchingMagnetics=LegacyMagnetics
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
