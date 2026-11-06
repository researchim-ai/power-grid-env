"""Averaged bidirectional active rectifier, DC capacitor and V/f induction drive.

Electrical machines use the separately qualified full Gamma winding equations.
This authored control is not an OEM PWM, FOC or semiconductor loss model.
"""
import math
import numpy as np
from power_grid_env.flux_machine import GammaWinding


class VfDrive:
    def __init__(self,parameters):
        self.p=parameters;self.machine=GammaWinding(parameters.motor);self.n=1+len(parameters.motor.cages)
        self.motor_at=7;self.speed_at=7+2*self.n;self.size=self.speed_at+1+(2 if parameters.motor.thermal else 0)

    def initial(self):
        x=np.zeros(self.size);x[0]=self.p.phase_rad;x[4]=.5*self.p.dc_capacitance_f*self.p.initial_dc_v**2
        x[self.speed_at]=self.p.motor.initial_speed_rad_s
        if self.p.motor.thermal:x[self.speed_at+1:]=self.p.motor.thermal.ambient_c
        return x

    def current(self,x):return complex(x[2],x[3])*np.exp(1j*x[0])

    def rates(self,t,voltage,x,connected):
        p=self.p;dx=np.zeros_like(x);vdc=math.sqrt(max(0.,2*x[4]/p.dc_capacitance_f))
        if x[4]<-1e-5:raise ValueError('DC energy left its physical nonnegative domain')
        currents=x[self.motor_at:self.speed_at].reshape(-1,2);currents=currents[:,0]+1j*currents[:,1]
        speed=x[self.speed_at];temperature=x[self.speed_at+1:] if p.motor.thermal else None
        frequency=p.motor_frequency_hz*min(max(t/p.ramp_s,0.),1.)
        magnitude=math.sqrt(2)*p.motor_ln_rms_v*min(frequency/p.motor_frequency_hz,1.)
        available=.5*vdc*p.maximum_modulation
        uv_scale=float(np.clip((vdc/p.dc_reference_v-.2)/.1,0.,1.))
        magnitude*=uv_scale
        applied=min(magnitude,available)*np.exp(1j*x[6])
        quantities=self.machine.quantities(currents,speed,temperature)
        rate,acceleration,thermal,ambient=self.machine.derivatives(currents,speed,temperature,applied,quantities)
        motor_power=float(1.5*(applied*np.conj(currents[0])).real)
        inverter_dc=motor_power/p.inverter_efficiency if motor_power>=0 else motor_power*p.inverter_efficiency
        bleed=vdc*vdc/p.bleed_ohm
        error=.5*p.dc_capacitance_f*p.dc_reference_v**2-x[4]
        wanted_dc=inverter_dc+bleed+p.dc_kp_per_s*error+x[5]
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
        dx[4]=rectifier_dc-inverter_dc-bleed
        dx[5]=p.dc_ki_per_s2*error if connected and (not limited or error*wanted_grid<0) else 0.
        dx[6]=2*np.pi*frequency;dx[self.motor_at:self.speed_at]=np.column_stack([rate.real,rate.imag]).ravel();dx[self.speed_at]=acceleration
        if p.motor.thermal:dx[self.speed_at+1:]=thermal
        mechanical=p.motor.fan_nm_per_rad_s2*abs(speed)**3+p.motor.viscous_nm_per_rad_s*speed**2
        stored=x[4]+quantities['energy_j']+.5*p.motor.inertia_kg_m2*speed**2
        copper=quantities['stator_copper_w']+quantities['rotor_copper_w']
        if p.motor.thermal:
            h=p.motor.thermal;stored+=h.stator_capacity_j_per_k*(temperature[0]-h.ambient_c)+h.rotor_capacity_j_per_k*(temperature[1]-h.ambient_c)
        loss=grid_power-rectifier_dc+inverter_dc-motor_power+bleed+(ambient if p.motor.thermal else copper)
        info=dict(dc_voltage_v=vdc,motor_speed_rad_s=float(speed),motor_current_peak_a=abs(currents[0]),motor_power_w=motor_power,grid_power_w=grid_power,
                  stored_j=float(stored),loss_w=float(loss),mechanical_power_w=float(mechanical),current_vector_peak_a=abs(actual),
                  inverter_frequency_hz=frequency,undervoltage_derated=uv_scale<1.,modulation_limited=magnitude>available,current_limited=limited,connected=connected)
        return dx,info


class PhaseMotor:
    """Direct isolated-star alpha-beta machine port; zero-sequence current blocked."""
    def __init__(self,parameters):
        self.p=parameters.passport;self.machine=GammaWinding(self.p);self.n=1+len(self.p.cages);self.at=2*self.n
        self.size=self.at+1+(2 if self.p.thermal else 0)

    def initial(self):
        x=np.zeros(self.size);x[self.at]=self.p.initial_speed_rad_s
        if self.p.thermal:x[self.at+1:]=self.p.thermal.ambient_c
        return x

    def current(self,x):return complex(x[0],x[1])

    def rates(self,t,voltage,x):
        z=x[:self.at].reshape(-1,2);currents=z[:,0]+1j*z[:,1];w=x[self.at];temperature=x[self.at+1:] if self.p.thermal else None
        q=self.machine.quantities(currents,w,temperature);di,dw,thermal,ambient=self.machine.derivatives(currents,w,temperature,voltage,q)
        dx=np.r_[np.column_stack([di.real,di.imag]).ravel(),dw,thermal if self.p.thermal else []]
        stored=q['energy_j']+.5*self.p.inertia_kg_m2*w*w
        copper=q['stator_copper_w']+q['rotor_copper_w']
        if self.p.thermal:
            h=self.p.thermal;stored+=h.stator_capacity_j_per_k*(temperature[0]-h.ambient_c)+h.rotor_capacity_j_per_k*(temperature[1]-h.ambient_c)
        info=dict(motor_power_w=float(1.5*(voltage*np.conj(currents[0])).real),motor_speed_rad_s=float(w),torque_nm=q['torque_nm'],
                  current_alpha_a=float(currents[0].real),current_beta_a=float(currents[0].imag),rotor_copper_w=q['rotor_copper_w'],
                  stored_j=float(stored),loss_w=ambient if self.p.thermal else copper,
                  mechanical_power_w=float(self.p.fan_nm_per_rad_s2*abs(w)**3+self.p.viscous_nm_per_rad_s*w*w))
        return dx,info
