"""Full native motulator 0.19.0 Gamma-machine + mechanical RHS, DOP853.

Primary uses physical T winding-flux matrix; native model uses independent Gamma
parameters. No stator state is eliminated and no RMS surrogate is compared.
"""
from pathlib import Path
import math,json,argparse,zipfile,hashlib
import numpy as np
from scipy.integrate import solve_ivp
from benchmarks.motulator_motor_validation import external_machine,VERSION,WHEEL_SHA256,PUBLISHED
from power_grid_env.stator_transient import StatorTransientPassport,simulate
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint

NAMES=('nominal','phase_shift','frequency60','source_resistance','light_load','locked_rotor')
LIMITS=dict(current_a=1e-5,torque_nm=1e-5,speed_rad_s=1e-5,flux_vs=1e-7)


def example(name):
    if name not in NAMES:raise ValueError('unknown stator transient case')
    beta=.95
    return StatorTransientPassport(stator_r_ohm=3.7,rotor_r_ohm=2.1/beta**2,stator_self_h=.245,rotor_self_h=.224/beta**2,mutual_h=.224/beta,pole_pairs=2,inertia_kg_m2=.015,
        fan_nm_per_rad_s2=(7. if name=='light_load' else 14.)/(2*math.pi*50/2)**2,source_r_ohm=.5 if name=='source_resistance' else .05,
        locked_rotor=name=='locked_rotor',source_ll_rms_v=400.,frequency_hz=60. if name=='frequency60' else 50.,source_phase_rad=.7 if name=='phase_shift' else 0.,duration_s=.5)


def reference(p,times):
    if p.source_l_h!=0:raise ValueError('native component reference covers resistive source impedance only')
    from motulator.drive.model import MechanicalSystem
    machine=external_machine();mechanics=MechanicalSystem(p.inertia_kg_m2,B_L=lambda w:p.fan_nm_per_rad_s2*abs(w))
    def outputs(t,x):
        machine.state.psi_s_ab=complex(x[0],x[1]);machine.state.psi_r_ab=complex(x[2],x[3])
        mechanics.state.w_M=complex(x[4]);machine.inp.w_M=x[4]
        current=machine.compute_outputs(machine.state,machine.inp)[0]
        machine.inp.u_s_ab=p.source_ll_rms_v*math.sqrt(2/3)*np.exp(1j*(2*math.pi*p.frequency_hz*t+p.source_phase_rad))-p.source_r_ohm*current
        machine.set_outputs(t);mechanics.inp.tau_M=machine.out.tau_M
        return current,float(machine.out.tau_M)
    def rhs(t,x):
        outputs(t,x);ds,dr=machine.rhs(t);_,dw=mechanics.rhs(t)
        return [ds.real,ds.imag,dr.real,dr.imag,0. if p.locked_rotor else float(np.real(dw))]
    sol=solve_ivp(rhs,(0,p.duration_s),np.zeros(5),method='DOP853',rtol=2e-11,atol=1e-12,max_step=min(p.step_s/2,1/(200*p.frequency_hz)),t_eval=times)
    if not sol.success:raise ValueError('native stator integration failed')
    channels={k:[] for k in ['current_alpha_a','current_beta_a','torque_nm','speed_rad_s','stator_flux_real_vs','stator_flux_imag_vs']}
    for t,x in zip(sol.t,sol.y.T):
        current,torque=outputs(t,x)
        for key,value in zip(channels,[current.real,current.imag,torque,x[4],x[0],x[1]]):channels[key].append(float(value))
    return dict(completed=True,version=VERSION,time_s=list(times),channels=channels,native_original_machine_and_mechanical_rhs=True,stator_flux_states_preserved=True)


def compare(primary,native):
    if not primary.get('completed') or not native.get('completed') or primary['time_s']!=native['time_s']:raise ValueError('incomplete component traces')
    errors={k:float(max(abs(np.asarray(v)-primary['channels'][k]))) for k,v in native['channels'].items()}
    limits={k:LIMITS['current_a'] if k.startswith('current_') else LIMITS['torque_nm'] if k=='torque_nm' else LIMITS['speed_rad_s'] if k=='speed_rad_s' else LIMITS['flux_vs'] for k in errors}
    return dict(passed=all(v<=limits[k] for k,v in errors.items()),errors=errors,limits=limits)


def run(out,wheel):
    import motulator
    if sha256_file(wheel)!=WHEEL_SHA256:raise ValueError('pinned motulator wheel hash mismatch')
    source=Path(motulator.__file__).parent;native_hashes={str(p.relative_to(source)):sha256_file(p) for p in source.rglob('*.py')}
    with zipfile.ZipFile(wheel) as z:
        for name,h in native_hashes.items():
            if hashlib.sha256(z.read('motulator/'+name)).hexdigest()!=h:raise ValueError('installed native source differs from pinned wheel')
    if out.exists() and any(out.iterdir()):raise ValueError('new stator-transient validation directory required')
    code=package_fingerprint();producer=sha256_file(Path(__file__))
    write_json(out/'protocol.json',dict(cases={n:example(n).model_dump(mode='json') for n in NAMES},limits=LIMITS,version=VERSION,wheel_sha256=WHEEL_SHA256,native_source_sha256=native_hashes,code_sha256=code,producer_sha256=producer))
    write_json(out/'summary.json',dict(passed=False,status='running'));checks=[]
    for name in NAMES:
        print(name,flush=True);p=example(name)
        # Native is solved independently on a declared axis before primary simulation.
        times=np.r_[np.arange(math.ceil(p.duration_s/p.step_s))*p.step_s,p.duration_s].tolist()
        n=reference(p,times);write_json(out/name/'native.json',n)
        r=simulate(p);write_json(out/name/'primary.json',r);c=compare(r,n);write_json(out/name/'comparison.json',c)
        if not c['passed']:raise ValueError('stator transient comparison failed '+name)
        checks.append(c)
    if package_fingerprint()!=code or sha256_file(Path(__file__))!=producer:raise ValueError('producer changed during stator validation')
    result=dict(passed=True,cases=len(checks),code_sha256=code,full_stator_transient_native_comparison=True,general_network_EMT_qualified=False,maximum_errors={k:max(c['errors'][k] for c in checks) for k in checks[0]['errors']},artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in out.rglob('*.json') if p!=out/'summary.json'})
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);p.add_argument('--wheel',required=True,type=Path);a=p.parse_args();print(run(a.out,a.wheel))
