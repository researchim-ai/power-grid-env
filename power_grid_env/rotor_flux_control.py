"""Sensored rotor-flux-oriented control for a linear single-cage Gamma machine.

Only measured stator current and mechanical speed enter the exact ZOH
current-model flux observer. No simulated winding flux is read by the controller.
Inverse-Gamma parameters: gamma=Lm/(Lm+Ll), LM=gamma*Lm, RR=gamma**2*Rr.
"""
import cmath
import math
import numpy as np
from pydantic import Field,model_validator
from power_grid_env.phase_network import Strict


class RotorFluxControl(Strict):
    flux_reference_vs: float = Field(gt=0)
    flux_ramp_s: float = Field(gt=0)
    speed_reference_rad_s: float
    speed_ramp_s: float = Field(gt=0)
    current_limit_peak_a: float = Field(gt=0)
    speed_kp_nm_per_rad_s: float = Field(gt=0)
    speed_ki_nm_per_rad: float = Field(gt=0)
    current_kp_ohm: float = Field(gt=0)
    current_ki_ohm_per_s: float = Field(gt=0)
    antiwindup_per_s: float = Field(gt=0)
    voltage_utilization: float = Field(default=.9,gt=0,le=1)
    torque_enable_flux_fraction: float = Field(default=.5,gt=0,le=1)
    provenance: str = Field(min_length=1)

    @model_validator(mode='after')
    def sourced(self):
        if not self.provenance.strip():raise ValueError('FOC parameter provenance required')
        return self


class RotorFluxController:
    def __init__(self,parameters,machine):
        self.p=parameters;self.machine=machine
        gamma=machine.unsaturated_h/(machine.unsaturated_h+machine.cages[0].leakage_h)
        self.lm=gamma*machine.unsaturated_h;self.rr=gamma**2*machine.cages[0].resistance_ohm
        self.lsigma=gamma*machine.cages[0].leakage_h
        self.flux=0j;self.integral=0j;self.speed_integral=0.;self.angle=0.

    def observe(self,current,speed,elapsed):
        a=-self.rr/self.lm+1j*self.machine.pole_pairs*speed
        z=a*elapsed
        gain=cmath.exp(z)
        integral=elapsed if abs(z)<1e-12 else (gain-1)/a
        self.flux=gain*self.flux+self.rr*integral*current

    def orientation_frequency(self,speed,measured,flux):
        return self.machine.pole_pairs*speed+self.rr*measured.imag/max(flux,.02*self.p.flux_reference_vs)

    def update(self,t,current,speed,vdc,period,enabled):
        p=self.p;flux=abs(self.flux)
        if flux>1e-10:self.angle=cmath.phase(self.flux)
        rotation=cmath.exp(1j*self.angle);measured=current/rotation
        flux_ref=p.flux_reference_vs*min(max(t/p.flux_ramp_s,0.),1.)
        direct=min(flux_ref/self.lm,p.current_limit_peak_a)
        qlimit=math.sqrt(max(0.,p.current_limit_peak_a**2-direct**2))
        speed_ref=p.speed_reference_rad_s*min(max(t/p.speed_ramp_s,0.),1.)
        error=speed_ref-speed
        torque_requested=p.speed_kp_nm_per_rad_s*error+self.speed_integral
        torque_gain=1.5*self.machine.pole_pairs*flux
        ready=flux>=p.torque_enable_flux_fraction*p.flux_reference_vs
        quadrature=float(np.clip(torque_requested/max(torque_gain,1e-12),-qlimit,qlimit)) if ready and enabled else 0.
        torque_reference=torque_gain*quadrature
        if enabled and ready:
            # Conditional integration prevents startup/current saturation from
            # winding the speed PI backward while the rotor flux is building.
            saturated=abs(torque_reference-torque_requested)>1e-10
            if not saturated or error*torque_requested<0:
                self.speed_integral+=period*p.speed_ki_nm_per_rad*error
        elif not enabled:self.speed_integral*=math.exp(-p.antiwindup_per_s*period)
        reference=complex(direct,quadrature) if enabled else 0j
        omega=self.orientation_frequency(speed,measured,flux)
        feedforward=self.machine.stator_r_ohm*reference+1j*omega*(self.lsigma*measured+flux)
        demanded=feedforward+p.current_kp_ohm*(reference-measured)+self.integral
        limit=max(0.,vdc)/math.sqrt(3)*p.voltage_utilization if enabled else 0.
        voltage=demanded if abs(demanded)<=limit else demanded*limit/abs(demanded)
        self.integral+=period*(p.current_ki_ohm_per_s*(reference-measured)+p.antiwindup_per_s*(voltage-demanded))
        return voltage*rotation,dict(flux_estimate_alpha_vs=self.flux.real,flux_estimate_beta_vs=self.flux.imag,
            flux_estimate_vs=flux,current_reference_peak_a=abs(reference),current_reference_d_a=reference.real,
            current_reference_q_a=reference.imag,speed_reference_rad_s=speed_ref,torque_reference_nm=torque_reference,
            torque_enabled=bool(ready and enabled),voltage_limited=abs(demanded)>limit,frame_angle_rad=self.angle)
