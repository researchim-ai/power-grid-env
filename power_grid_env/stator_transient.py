"""Balanced direct-fed induction machine with stator AND rotor flux states.

Stationary alpha-beta peak SI space vectors, constant inductances and one
series RL source. Component profile: no switching/open-circuit arc, saturation,
unbalance, converter, winding temperature or general-network EMT claim.
"""
from pathlib import Path
import argparse,json,math
import numpy as np
from scipy.integrate import solve_ivp
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.evidence import package_fingerprint
from power_grid_env.runtime_limits import RuntimeLimits,IntegrationBudget,RuntimeLimitExceeded
from power_grid_env.report import write_json


class StatorTransientPassport(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    stator_r_ohm: float=Field(gt=0)
    rotor_r_ohm: float=Field(gt=0)
    stator_self_h: float=Field(gt=0)
    rotor_self_h: float=Field(gt=0)
    mutual_h: float=Field(gt=0)
    pole_pairs: int=Field(ge=1,le=20)
    inertia_kg_m2: float=Field(gt=0)
    locked_rotor: bool=False
    fan_nm_per_rad_s2: float=Field(default=0.,ge=0)
    source_r_ohm: float=Field(default=0.,ge=0)
    source_l_h: float=Field(default=0.,ge=0)
    source_ll_rms_v: float=Field(gt=0)
    frequency_hz: float=Field(gt=0,le=1000)
    source_phase_rad: float=0.
    duration_s: float=Field(default=.5,gt=0,le=10)
    step_s: float=Field(default=.00025,gt=0)
    rtol: float=Field(default=1e-9,gt=0,le=1e-5)
    atol: float=Field(default=1e-11,gt=0,le=1e-7)
    runtime_limits: RuntimeLimits=Field(default_factory=RuntimeLimits)

    @model_validator(mode='after')
    def physical(self):
        if self.stator_self_h<=self.mutual_h or self.rotor_self_h<=self.mutual_h or self.stator_self_h*self.rotor_self_h<=self.mutual_h**2:raise ValueError('positive self/leakage inductances and inductance determinant required')
        if self.step_s>self.duration_s or math.ceil(self.duration_s/self.step_s)>100000:raise ValueError('stator-transient sampling budget')
        return self


def simulate(p):
    budget=IntegrationBudget(p.runtime_limits)
    L=np.array([[p.stator_self_h+p.source_l_h,p.mutual_h],[p.mutual_h,p.rotor_self_h]])
    inverse=np.linalg.inv(L);wb=2*math.pi*p.frequency_hz
    def quantities(t,x):
        flux=np.array([complex(x[0],x[1]),complex(x[2],x[3])]);is_,ir=inverse@flux
        motor_flux=flux[0]-p.source_l_h*is_
        torque=1.5*p.pole_pairs*(is_*np.conj(motor_flux)).imag
        source=p.source_ll_rms_v*math.sqrt(2/3)*np.exp(1j*(wb*t+p.source_phase_rad))
        return flux,is_,ir,float(torque),source
    def rhs(t,x):
        flux,is_,ir,torque,source=quantities(t,x)
        ds=source-(p.stator_r_ohm+p.source_r_ohm)*is_
        dr=-p.rotor_r_ohm*ir+1j*p.pole_pairs*x[4]*flux[1]
        mechanical=0. if p.locked_rotor else (torque-p.fan_nm_per_rad_s2*x[4]*abs(x[4]))/p.inertia_kg_m2
        return [ds.real,ds.imag,dr.real,dr.imag,mechanical]
    times=np.r_[np.arange(math.ceil(p.duration_s/p.step_s))*p.step_s,p.duration_s]
    channels={k:[] for k in ['stator_flux_real_vs','stator_flux_imag_vs','rotor_flux_real_vs','rotor_flux_imag_vs','speed_rad_s','torque_nm','current_alpha_a','current_beta_a','phase_a_a','phase_b_a','phase_c_a','current_space_vector_rms_equivalent_a','magnetic_energy_j']}
    try:
        budget.record(len(times)*(len(channels)+7))
        sol=solve_ivp(budget.rhs(rhs),(0,p.duration_s),np.zeros(5),method='Radau',rtol=p.rtol,atol=p.atol,max_step=min(p.step_s,1/(100*p.frequency_hz)),t_eval=times)
        if not sol.success or not np.isfinite(sol.y).all():return dict(completed=False,status='numerically_not_converged')
        for t,x in zip(sol.t,sol.y.T):
            budget.check_time()
            flux,is_,ir,torque,_=quantities(t,x);motor_flux=flux[0]-p.source_l_h*is_
            # 3/4 factor for amplitude-invariant peak space vectors; includes source L.
            energy=.75*(np.conj(is_)*flux[0]+np.conj(ir)*flux[1]).real
            values=[motor_flux.real,motor_flux.imag,flux[1].real,flux[1].imag,x[4],torque,is_.real,is_.imag,is_.real,-.5*is_.real+math.sqrt(3)/2*is_.imag,-.5*is_.real-math.sqrt(3)/2*is_.imag,abs(is_)/math.sqrt(2),energy]
            for key,v in zip(channels,values):channels[key].append(float(v))
    except RuntimeLimitExceeded as e:return dict(completed=False,status='runtime_limit_exceeded',reason=e.resource,runtime_budget=budget.report())
    except (ValueError,ArithmeticError,np.linalg.LinAlgError) as e:return dict(completed=False,status='numerically_not_converged',error=str(e))
    return dict(completed=True,status='completed',model='balanced_full_stator_rotor_flux_component',code_sha256=package_fingerprint(),settings=p.model_dump(mode='json'),time_s=times.tolist(),channels=channels,runtime_budget=budget.report(),
                current_convention='phase currents are instantaneous A; |space vector|/sqrt(2) is amplitude-equivalent RMS, not a transient cycle-window RMS',general_network_EMT_qualified=False,industrial_profile_qualified=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('passport',type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    r=simulate(StatorTransientPassport.model_validate_json(a.passport.read_text()));write_json(a.out,r)
    raise SystemExit(0 if r['completed'] else 1)


if __name__=='__main__':main()
