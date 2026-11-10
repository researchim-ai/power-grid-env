"""Active or resistive diode rectifier, DC link and averaged induction drive.

Electrical machines use the separately qualified full Gamma winding equations.
V/f or prescribed-frame PI current control; not OEM PWM or rotor-flux FOC.
"""
import math
from types import SimpleNamespace
import numpy as np
from power_grid_env.flux_machine import GammaWinding
from power_grid_env.phase_core_motor import PhaseCoreMotor


class VfDrive:
    def __init__(self,parameters):
        self.p=parameters;self.n=1+len(parameters.motor.cages)
        port=SimpleNamespace(passport=parameters.motor,initial_currents_ab_a=None,initial_fluxes_ab_vs=parameters.motor_initial_fluxes_ab_vs)
        self.motor_model=PhaseCoreMotor(port) if parameters.motor.core_loss_s else PhaseMotor(port)
        self.machine=None if parameters.motor.core_loss_s else self.motor_model.machine
        self.motor_at=9 if parameters.current_control is not None else 7;self.speed_at=self.motor_at+2*self.n;self.size=self.speed_at+1+(2 if parameters.motor.thermal else 0)

    def initial(self):
        x=np.zeros(self.size);x[0]=self.p.phase_rad;x[4]=.5*self.p.dc_capacitance_f*self.p.initial_dc_v**2
        if self.p.diode_front_end is not None:x[4]=self.p.initial_dc_v
        x[self.motor_at:]=self.motor_model.initial()
        return x

    def current(self,x):return complex(x[2],x[3])*np.exp(1j*x[0])

    def rates(self,t,voltage,x,connected,precharge_bypassed=None):
        p=self.p;dx=np.zeros_like(x)
        passive=p.diode_front_end is not None
        if passive and precharge_bypassed is None:precharge_bypassed=p.diode_front_end.precharge_bypassed
        vdc=max(0.,x[4]) if passive else math.sqrt(max(0.,2*x[4]/p.dc_capacitance_f))
        dc_energy=.5*p.dc_capacitance_f*vdc*vdc if passive else x[4]
        if x[4]<-1e-5:raise ValueError('DC voltage left its physical nonnegative domain' if passive else 'DC energy left its physical nonnegative domain')
        speed=x[self.speed_at];temperature=x[self.speed_at+1:] if p.motor.thermal else None
        frequency=p.motor_frequency_hz*min(max(t/p.ramp_s,0.),1.)
        magnitude=math.sqrt(2)*p.motor_ln_rms_v*min(frequency/p.motor_frequency_hz,1.)
        available=.5*vdc*p.maximum_modulation
        uv_scale=float(np.clip((vdc/p.dc_reference_v-.2)/.1,0.,1.))
        magnitude*=uv_scale
        applied=min(magnitude,available)*np.exp(1j*x[6])
        control_info={}
        if p.current_control is not None:
            from power_grid_env.inverter_current_control import current_control
            motor_state=x[self.motor_at:]
            offset,conductance=self.motor_model.terminal_relation(motor_state) if p.motor.core_loss_s else (self.motor_model.current(motor_state),0.)
            applied,integral_rate,control_info=current_control(p.current_control,t,x[6],2*np.pi*frequency,complex(x[7],x[8]),offset,conductance,available*uv_scale)
            dx[7:9]=[integral_rate.real,integral_rate.imag]
        motor_rate,motor_info=self.motor_model.rates(t,applied,x[self.motor_at:])
        current=complex(motor_info['current_alpha_a'],motor_info['current_beta_a'])
        motor_power=motor_info['motor_power_w']
        inverter_dc=motor_power/p.inverter_efficiency if motor_power>=0 else motor_power*p.inverter_efficiency
        bleed=vdc*vdc/p.bleed_ohm
        braking_duty=0. if p.braking_resistance_ohm is None else float(np.clip((vdc-p.braking_start_v)/(p.braking_full_v-p.braking_start_v),0.,1.))
        braking_power=0. if p.braking_resistance_ohm is None else braking_duty*vdc*vdc/p.braking_resistance_ohm
        error=.5*p.dc_capacitance_f*p.dc_reference_v**2-dc_energy
        wanted_dc=inverter_dc+bleed+braking_power+p.dc_kp_per_s*error+x[5]
        wanted_grid=wanted_dc/p.rectifier_efficiency if wanted_dc>=0 else wanted_dc*p.rectifier_efficiency
        reference=-wanted_grid/(1.5*max(abs(voltage),.1*math.sqrt(2)*p.grid_ln_rms_v))
        limit=math.sqrt(2)*p.grid_current_limit_rms_a;limited=abs(reference)>limit
        reference=float(np.clip(reference,-limit,limit)) if connected else 0.
        actual=self.current(x) if connected else 0j
        grid_power=float(-1.5*(voltage*np.conj(actual)).real)
        rectifier_dc=grid_power*p.rectifier_efficiency if grid_power>=0 else grid_power/p.rectifier_efficiency
        rotated=voltage*np.exp(-1j*x[0]);pll_error=rotated.imag/max(abs(voltage),.1*math.sqrt(2)*p.grid_ln_rms_v)
        correction=p.pll_kp_per_s*pll_error+x[1];cap=2*np.pi*5
        dx[0]=2*np.pi*p.grid_frequency_hz+np.clip(correction,-cap,cap)
        dx[1]=p.pll_ki_per_s2*pll_error if abs(correction)<cap or correction*pll_error<0 else 0.
        dx[2]=(reference-x[2])/p.rectifier_tau_s;dx[3]=-x[3]/p.rectifier_tau_s
        dx[4]=rectifier_dc-inverter_dc-bleed-braking_power
        dx[5]=p.dc_ki_per_s2*error if connected and (not limited or error*wanted_grid<0) else 0.
        bridge_info={}
        if passive:
            from power_grid_env.diode_front_end import bridge_port
            actual,bridge_info=bridge_port(voltage,vdc,p.diode_front_end,connected=connected,bypassed=precharge_bypassed)
            grid_power=bridge_info['grid_power_w'];rectifier_dc=bridge_info['rectifier_dc_power_w']
            if vdc==0. and inverter_dc!=0.:raise ValueError('nonzero inverter power at zero DC voltage')
            inverter_current=inverter_dc/vdc if vdc else 0.
            brake_current=braking_duty*vdc/p.braking_resistance_ohm if p.braking_resistance_ohm is not None else 0.
            dx[:4]=0.;dx[5]=0.
            dx[4]=(bridge_info['rectifier_current_a']-inverter_current-vdc/p.bleed_ohm-brake_current)/p.dc_capacitance_f
            limited=False
        dx[6]=2*np.pi*frequency;dx[self.motor_at:]=motor_rate
        mechanical=p.motor.fan_nm_per_rad_s2*abs(speed)**3+p.motor.viscous_nm_per_rad_s*speed**2
        stored=dc_energy+motor_info['stored_j']
        rectifier_loss=grid_power-rectifier_dc;inverter_loss=inverter_dc-motor_power
        loss=rectifier_loss+inverter_loss+bleed+braking_power+motor_info['loss_w']
        info=dict(dc_voltage_v=vdc,motor_speed_rad_s=float(speed),motor_current_peak_a=abs(current),motor_power_w=motor_power,grid_power_w=grid_power,
                  stored_j=float(stored),loss_w=float(loss),mechanical_power_w=float(mechanical),current_vector_peak_a=abs(actual),
                  inverter_frequency_hz=frequency,undervoltage_derated=uv_scale<1.,modulation_limited=magnitude>available,current_limited=limited,connected=connected)
        info.update(braking_duty=braking_duty,braking_power_w=float(braking_power),dc_energy_j=float(dc_energy),rectifier_dc_power_w=float(rectifier_dc),inverter_dc_power_w=float(inverter_dc),rectifier_loss_w=float(rectifier_loss),inverter_loss_w=float(inverter_loss),bleed_loss_w=float(bleed),motor_stored_j=motor_info['stored_j'],motor_core_loss_w=motor_info['core_loss_w'],motor_stator_copper_w=motor_info['stator_copper_w'],motor_rotor_copper_w=motor_info['rotor_copper_w'],motor_torque_nm=motor_info['torque_nm'],motor_stator_flux_alpha_vs=motor_info['stator_flux_alpha_vs'],motor_stator_flux_beta_vs=motor_info['stator_flux_beta_vs'],motor_current_alpha_a=current.real,motor_current_beta_a=current.imag)
        info.update(bridge_info)
        info.update(control_info)
        if p.current_control is not None:info['modulation_limited']=control_info['current_control_limited']
        if p.motor.thermal:info.update(motor_stator_temperature_c=float(temperature[0]),motor_rotor_temperature_c=float(temperature[1]))
        return dx,info


class PhaseMotor:
    """Direct isolated-star alpha-beta machine port; zero-sequence current blocked."""
    def __init__(self,parameters):
        self.p=parameters.passport;self.initial_currents=parameters.initial_currents_ab_a;self.initial_flux=parameters.initial_fluxes_ab_vs;self.machine=GammaWinding(self.p);self.n=1+len(self.p.cages);self.at=2*self.n
        self.size=self.at+1+(2 if self.p.thermal else 0)

    def initial(self):
        x=np.zeros(self.size);x[self.at]=self.p.initial_speed_rad_s
        if self.initial_currents is not None:x[:self.at]=np.array(self.initial_currents).ravel()
        if self.initial_flux is not None:
            flux=np.array([complex(*z) for z in self.initial_flux]);psi=flux[0];ir=(flux[1:]-psi)/self.machine.leakage
            stator=psi*(1+(self.p.saturation_beta_per_vs*abs(psi))**self.p.saturation_exponent)/self.p.unsaturated_h-sum(ir)
            currents=np.r_[stator,ir];x[:self.at]=np.column_stack([currents.real,currents.imag]).ravel()
        if self.p.thermal:x[self.at+1:]=self.p.thermal.ambient_c
        return x

    def current(self,x):return complex(x[0],x[1])

    def quantities(self,x,voltage):
        z=x[:self.at].reshape(-1,2);currents=z[:,0]+1j*z[:,1]
        return self.machine.quantities(currents,x[self.at],x[self.at+1:] if self.p.thermal else None)

    def rates(self,t,voltage,x):
        z=x[:self.at].reshape(-1,2);currents=z[:,0]+1j*z[:,1];w=x[self.at];temperature=x[self.at+1:] if self.p.thermal else None
        q=self.machine.quantities(currents,w,temperature);di,dw,thermal,ambient=self.machine.derivatives(currents,w,temperature,voltage,q)
        dx=np.r_[np.column_stack([di.real,di.imag]).ravel(),dw,thermal if self.p.thermal else []]
        stored=q['energy_j']+.5*self.p.inertia_kg_m2*w*w
        copper=q['stator_copper_w']+q['rotor_copper_w']
        if self.p.thermal:
            h=self.p.thermal;stored+=h.stator_capacity_j_per_k*(temperature[0]-h.ambient_c)+h.rotor_capacity_j_per_k*(temperature[1]-h.ambient_c)
        info=dict(motor_power_w=float(1.5*(voltage*np.conj(currents[0])).real),motor_speed_rad_s=float(w),torque_nm=q['torque_nm'],
                  current_alpha_a=float(currents[0].real),current_beta_a=float(currents[0].imag),rotor_copper_w=q['rotor_copper_w'],stator_copper_w=q['stator_copper_w'],core_loss_w=0.,stator_flux_alpha_vs=float(q['psi'].real),stator_flux_beta_vs=float(q['psi'].imag),
                  stored_j=float(stored),loss_w=ambient if self.p.thermal else copper,
                  mechanical_power_w=float(self.p.fan_nm_per_rad_s2*abs(w)**3+self.p.viscous_nm_per_rad_s*w*w))
        return dx,info
