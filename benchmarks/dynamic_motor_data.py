"""Independent original motulator flux/mechanics traces; authored excitation.

Published Aalto electrical passports are copied from the traceable catalog.
These are synthetic simulator observations, not measured hardware recordings.
No production winding RHS, inverse equations or identification solver generates
observations. The caller verifies the original motulator wheel before acceptance.
"""
import math
import numpy as np
from scipy.integrate import solve_ivp
from power_grid_env.dynamic_motor_identification import DynamicMotorTrace,DynamicMotorIdentification
from power_grid_env.motor_catalog import catalog_entry


def trace(passport,id,frequency,voltage,duration=.24,sag=.75):
    from motulator.drive.model import InductionMachine,InductionMachinePars,MechanicalSystem
    p=passport;winding=p.cages[0]
    machine=InductionMachine(InductionMachinePars(n_p=p.pole_pairs,R_s=p.stator_r_ohm,R_r=winding.resistance_ohm,L_ell=winding.leakage_h,L_s=lambda amplitude:p.unsaturated_h/(1+(p.saturation_beta_per_vs*amplitude)**p.saturation_exponent)))
    mechanical=MechanicalSystem(p.inertia_kg_m2,B_L=lambda w:p.fan_nm_per_rad_s2*abs(w)+p.viscous_nm_per_rad_s)
    times=np.linspace(0.,duration,round(duration/.0002)+1)
    envelope=np.interp(times,[0.,.10,.12,duration],[1.,1.,sag,sag])
    def voltage_at(t):return math.sqrt(2/3)*voltage*np.interp(t,[0.,.10,.12,duration],[1.,1.,sag,sag])*np.exp(2j*math.pi*frequency*t)
    def rhs(t,x):
        machine.state.psi_s_ab=complex(*x[:2]);machine.state.psi_r_ab=complex(*x[2:4]);machine.inp.w_M=x[4];machine.inp.u_s_ab=voltage_at(t)
        machine.set_outputs(t);mechanical.state.w_M=complex(x[4]);mechanical.inp.tau_M=machine.out.tau_M
        ds,dr=machine.rhs(t);_,dw=mechanical.rhs(t)
        return [ds.real,ds.imag,dr.real,dr.imag,0. if p.locked_rotor else float(np.real(dw))]
    sol=solve_ivp(rhs,(0.,duration),[0.,0.,0.,0.,p.initial_speed_rad_s],t_eval=times,rtol=2e-11,atol=2e-13,method='DOP853',max_step=.0002)
    if not sol.success:raise ValueError('native trace did not complete')
    current=[]
    for x in sol.y.T:
        machine.state.psi_s_ab=complex(*x[:2]);machine.state.psi_r_ab=complex(*x[2:4]);machine.inp.w_M=x[4]
        current.append(machine.compute_outputs(machine.state,machine.inp)[0])
    current=np.array(current);u=math.sqrt(2/3)*voltage*envelope*np.exp(2j*math.pi*frequency*times)
    return DynamicMotorTrace(id=id,provenance='Synthetic original motulator 0.19.0; published Aalto electrical model; authored J, load and voltage envelope',frequency_hz=frequency,time_s=times.tolist(),voltage_alpha_v=u.real.tolist(),voltage_beta_v=u.imag.tolist(),current_alpha_a=current.real.tolist(),current_beta_a=current.imag.tolist(),speed_rad_s=sol.y[4].tolist(),current_sigma_a=.002,speed_sigma_rad_s=.005)


def example(kind='linear'):
    if kind not in {'linear','saturated','viscous','locked'}:raise ValueError('registered dynamic identification profile required')
    p=catalog_entry('aalto-linear-fan' if kind in {'linear','locked'} else 'aalto-saturated-fan')['motor']
    if kind=='locked':p.locked_rotor=True
    if kind=='viscous':p.fan_nm_per_rad_s2=0.;p.viscous_nm_per_rad_s=14.6/(math.pi*50)
    seed=p.model_copy(deep=True)
    seed.stator_r_ohm*=1.12;seed.unsaturated_h*=.9;seed.cages[0].resistance_ohm*=.88;seed.cages[0].leakage_h*=1.1;seed.inertia_kg_m2*=1.3;seed.fan_nm_per_rad_s2*=.7;seed.viscous_nm_per_rad_s*=.7
    if kind=='locked':seed.inertia_kg_m2=p.inertia_kg_m2;seed.fan_nm_per_rad_s2=p.fan_nm_per_rad_s2
    parameters=['stator_r_ohm','unsaturated_h','rotor_r_ohm','rotor_leakage_h']
    if kind in {'saturated','viscous'}:seed.saturation_beta_per_vs*=.93;parameters.append('saturation_beta_per_vs')
    return DynamicMotorIdentification(initial=seed,electrical_parameters=parameters,mechanical_load='fixed' if kind=='locked' else 'viscous' if kind=='viscous' else 'fan',fitting=[trace(p,'train-50',50.,400.),trace(p,'train-35',35.,260.,sag=.9)],validation=[trace(p,'holdout-45',45.,330.,sag=.85)]),p
