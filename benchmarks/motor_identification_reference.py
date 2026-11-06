"""Synthetic held-out identification from original motulator steady flux RHS.

No fitted-current equation is used to generate the observations. This is a
numerical recovery experiment, not a claim of experimental identification.
"""
import math
import numpy as np
from scipy.optimize import root
from power_grid_env.motor_identification import MotorIdentification,identify
from benchmarks.flux_network_native import example


def example_identification():
    from motulator.drive.model import InductionMachine,InductionMachinePars
    _,settings=example('core_radial');p=settings.motors[0]
    m=InductionMachine(InductionMachinePars(n_p=p.pole_pairs,R_s=p.stator_r_ohm,R_r=p.cages[0].resistance_ohm,L_ell=p.cages[0].leakage_h,
        L_s=lambda rho:p.unsaturated_h/(1+(p.saturation_beta_per_vs*rho)**p.saturation_exponent),G_c=p.core_loss_s))
    rows=[]
    for hz in [15.,30.,50.,75.]:
        w=2*math.pi*hz
        for ratio in [.3,.7,1.1]:
            for slip in [.03,.3,1.]:
                voltage=ratio*w;m.inp.u_s_ab=complex(voltage);m.inp.w_M=w*(1-slip)/p.pole_pairs
                def rhs(x):
                    m.state.psi_s_ab=complex(*x[:2]);m.state.psi_r_ab=complex(*x[2:]);m.set_outputs(0.);ds,dr=m.rhs(0.)
                    errors=[ds-1j*w*m.state.psi_s_ab,dr-1j*w*m.state.psi_r_ab]
                    return np.array([[e.real,e.imag] for e in errors]).ravel()
                sol=root(rhs,[0.,-ratio,0.,-ratio],tol=1e-11)
                if np.max(abs(rhs(sol.x)))>1e-7:raise ValueError('native steady-state solve did not converge')
                current=m.out.i_s_ab
                rows.append(dict(frequency_hz=hz,mechanical_rad_s=m.inp.w_M,voltage_alpha_v=voltage,voltage_beta_v=0.,current_alpha_a=float(current.real),current_beta_a=float(current.imag),sigma_a=.001))
    seed=p.model_copy(deep=True);seed.stator_r_ohm*=1.1;seed.unsaturated_h*=.9;seed.saturation_beta_per_vs*=1.1;seed.core_loss_s*=1.15
    seed.cages[0].resistance_ohm*=.85;seed.cages[0].leakage_h*=1.1
    spec=MotorIdentification(initial=seed,fitting=rows[::2],validation=rows[1::2],data_provenance='Synthetic original motulator 0.19.0 steady Gamma RHS; not laboratory measurements')
    return p,spec


def run(out,wheel):
    from pathlib import Path
    from power_grid_env.report import write_json,sha256_file
    from power_grid_env.evidence import package_fingerprint
    from benchmarks.extended_equipment_validation import verify_native
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise ValueError('fresh identification output required')
    hashes=verify_native(wheel);code=package_fingerprint();producer=sha256_file(Path(__file__))
    p,s=example_identification();write_json(out/'observations.json',s.model_dump(mode='json'))
    write_json(out/'protocol.json',dict(true_passport=p.model_dump(mode='json'),wheel_sha256=sha256_file(wheel),native_sources=hashes,code_sha256=code,producer_sha256=producer,parameter_relative_tolerance=1e-6))
    result=identify(s);write_json(out/'identification.json',result)
    actual=result['passport'];errors=[abs(actual[k]/getattr(p,k)-1) for k in ['stator_r_ohm','unsaturated_h','saturation_beta_per_vs','core_loss_s']]
    errors += [abs(actual['cages'][0][k]/getattr(p.cages[0],k)-1) for k in ['resistance_ohm','leakage_h']]
    if code!=package_fingerprint() or producer!=sha256_file(Path(__file__)):raise ValueError('identification producer changed')
    summary=dict(passed=result['passed'] and max(errors)<1e-6,code_sha256=code,maximum_relative_parameter_error=max(errors),synthetic_native_data=True,field_validated=False,
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in out.glob('*.json') if p.name!='summary.json'})
    write_json(out/'summary.json',summary);return summary

if __name__=='__main__':
    import argparse,json
    from pathlib import Path
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);parser.add_argument('--wheel',type=Path,required=True);a=parser.parse_args();r=run(a.out,a.wheel);print(json.dumps(r,indent=2));raise SystemExit(0 if r['passed'] else 1)
