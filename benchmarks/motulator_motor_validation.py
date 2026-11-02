"""Published Aalto 2.2-kW motor: analytic T circuit vs primary vs native motulator.

Only constant-speed sinusoidal steady states are compared. No stator-transient
model is removed from motulator; rotating steady states solve its original RHS.
"""
from pathlib import Path
import argparse,itertools,math,sys
import numpy as np
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint

SOURCE='https://aalto-electric-drives.github.io/motulator/drive_examples/grid_connection/plot_2kw_im_dol.html'
VERSION='0.19.0'
WHEEL_SHA256='f2aae0c1d0caf435f0bb9d7b87eda781bc4e4bc9fc7439122fb1b64fd42448eb'
PUBLISHED=dict(n_p=2,R_s=3.7,R_R=2.1,L_sgm=.021,L_M=.224)
SPEEDS=(0.,.2,.8,.97,1.,1.03,1.2)
LIMITS=dict(voltage_v=1e-7,current_a=1e-7,torque_nm=1e-7,p_w=1e-5,q_var=1e-5)
Z_EXTERNAL=.05+.1j


def t_parameters(beta):
    if not PUBLISHED['L_M']/(PUBLISHED['L_M']+PUBLISHED['L_sgm'])<beta<1:raise ValueError('positive T leakage referral required')
    p=PUBLISHED
    return dict(rs=p['R_s'],rr=p['R_R']/beta**2,lm=p['L_M']/beta,ls=p['L_sgm']+p['L_M']-p['L_M']/beta,lr=p['L_M']*(1-beta)/beta**2)


def analytic(frequency,voltage_pu,speed,beta):
    p=t_parameters(beta);wb=2*math.pi*frequency;slip=1-speed
    rotor_y=slip/complex(p['rr'],wb*p['lr']*slip)
    parallel=1/(1/(1j*wb*p['lm'])+rotor_y)
    current=400/math.sqrt(3)*voltage_pu/(Z_EXTERNAL+p['rs']+1j*wb*p['ls']+parallel)
    voltage=400/math.sqrt(3)*voltage_pu-Z_EXTERNAL*current
    torque=3*PUBLISHED['n_p']/wb*(p['rr']*slip*abs(parallel*current)**2/abs(complex(p['rr'],wb*p['lr']*slip))**2)
    return physical(voltage,current,torque)


def physical(voltage,current,torque):
    power=3*voltage*np.conj(current)
    return dict(voltage_v=complex(voltage),current_a=complex(current),torque_nm=float(torque),p_w=float(power.real),q_var=float(power.imag))


def primary(frequency,voltage_pu,speed,beta,system_va):
    from power_grid_env.build import NetBuilder
    from power_grid_env.induction import InductionMotor,InductionSettings
    from power_grid_env.induction_network import InductionNetwork
    p=t_parameters(beta);wb=2*math.pi*frequency;motor_va=math.sqrt(3)*400*5;zb=400**2/motor_va
    b=NetBuilder('published-aalto-motor','Published motor steady-speed reference',.4,s_base_va=system_va,frequency_hz=frequency)
    b.node('grid',.4);b.node('motor',.4);b.source('grid',.02,.04,ll_v=400*voltage_pu);b.line_z1('grid','motor',.03+.06j,line_id='feeder')
    m=InductionMotor(id='m',node_id='motor',rated_va=motor_va,rated_ll_v=400.,stator_r_pu=p['rs']/zb,rotor_r_pu=p['rr']/zb,stator_x_pu=wb*p['ls']/zb,rotor_x_pu=wb*p['lr']/zb,magnetizing_x_pu=wb*p['lm']/zb,inertia_s=.5*.03*(wb/2)**2/motor_va,fan_torque_pu=14.6*(wb/2)/motor_va)
    model=InductionNetwork(b.build(),InductionSettings(motors=[m]))
    v,i,em,torque=model.steady_electrical(np.array([speed]));at=model.motor_buses[0]
    return physical(v[at]*400/math.sqrt(3),i[0]*motor_va/(math.sqrt(3)*400),torque[0]*motor_va/(wb/2))


def external_machine():
    from importlib.metadata import version
    if version('motulator')!=VERSION:raise ValueError('pinned motulator 0.19.0 required')
    from motulator.drive.model import InductionMachine,InductionMachineInvGammaPars
    return InductionMachine(InductionMachineInvGammaPars(**PUBLISHED))


def external(frequency,voltage_pu,speed):
    machine=external_machine();wb=2*math.pi*frequency
    machine.inp.w_M=wb*speed/PUBLISHED['n_p']
    source=400*math.sqrt(2/3)*voltage_pu
    def residual(flux):
        machine.state.psi_s_ab,machine.state.psi_r_ab=flux
        # G_c=0: current is independent of voltage, so series source Z is exact.
        current=machine.compute_outputs(machine.state,machine.inp)[0]
        machine.inp.u_s_ab=source-Z_EXTERNAL*current
        machine.set_outputs(0.)
        return np.array(machine.rhs(0.))-1j*wb*np.array(flux)
    offset=residual([0j,0j]);matrix=np.column_stack([residual([1.+0j,0j])-offset,residual([0j,1.+0j])-offset])
    flux=np.linalg.solve(matrix,-offset);error=float(np.max(abs(residual(flux))))
    if not math.isfinite(error) or error>1e-9:raise ValueError('native motulator steady RHS residual exceeded')
    current,_,torque=machine.compute_outputs(machine.state,machine.inp)
    return physical(machine.inp.u_s_ab/math.sqrt(2),current/math.sqrt(2),torque),error


def pack(row):
    return {k:dict(real=v.real,imag=v.imag) if isinstance(v,complex) else v for k,v in row.items()}


def run(out,wheel):
    out=Path(out)
    if (out/'protocol.json').exists():raise ValueError('use a fresh motulator output directory')
    import motulator,zipfile
    wheel=Path(wheel)
    if sha256_file(wheel)!=WHEEL_SHA256:raise ValueError('published motulator wheel hash mismatch')
    source_root=Path(motulator.__file__).parent
    native_hashes={str(p.relative_to(source_root)):sha256_file(p) for p in sorted(source_root.rglob('*.py'))}
    with zipfile.ZipFile(wheel) as archive:
        for p,h in native_hashes.items():
            import hashlib
            if hashlib.sha256(archive.read('motulator/'+p)).hexdigest()!=h:raise ValueError('installed native source differs from pinned wheel')
    code=package_fingerprint();producer=sha256_file(Path(__file__))
    write_json(out/'protocol.json',dict(schema='power-grid-env.motulator-steady/v1',source=SOURCE,version=VERSION,wheel_sha256=WHEEL_SHA256,published_parameters=PUBLISHED,frequency_hz=[50.,60.],voltage_pu=[.8,1.,1.1],speeds_pu=list(SPEEDS),rotor_referral_factors=[.95,.98],network_bases_va=[5000.,10000.],limits=LIMITS,external_series_impedance_ohm=dict(real=Z_EXTERNAL.real,imag=Z_EXTERNAL.imag),native_steady_RHS_limit=1e-9,native_source_sha256=native_hashes,producer_sha256=producer,code_sha256=code,full_stator_transient_comparison=False))
    write_json(out/'summary.json',dict(passed=False,status='running'))
    try:
        rows=[]
        for frequency,voltage,speed,beta,system_va in itertools.product([50.,60.],[.8,1.,1.1],SPEEDS,[.95,.98],[5000.,10000.]):
            a=analytic(frequency,voltage,speed,beta);p=primary(frequency,voltage,speed,beta,system_va);n,rhs=external(frequency,voltage,speed)
            errors={label+':'+key:float(abs(values[key]-a[key])) for label,values in [('primary',p),('motulator',n)] for key in LIMITS}
            passed=all(math.isfinite(v) and v<=LIMITS[k.split(':')[1]] for k,v in errors.items())
            rows.append(dict(frequency_hz=frequency,voltage_pu=voltage,speed_pu=speed,referral_factor=beta,system_va=system_va,analytic=pack(a),primary=pack(p),motulator=pack(n),native_rhs_residual=rhs,errors=errors,passed=passed))
        if code!=package_fingerprint() or producer!=sha256_file(Path(__file__)) or native_hashes!={str(p.relative_to(source_root)):sha256_file(p) for p in sorted(source_root.rglob('*.py'))}:raise ValueError('benchmark producer changed')
        summary=dict(passed=all(r['passed'] for r in rows),status='complete',points=len(rows),maximum_errors={k:max(r['errors'][k] for r in rows) for k in rows[0]['errors']},maximum_native_rhs=max(r['native_rhs_residual'] for r in rows),rows=rows,measured_data=False,field_validated=False,full_stator_transient_model_qualified=False,protocol_sha256=sha256_file(out/'protocol.json'))
        write_json(out/'summary.json',summary);return summary
    except Exception as exc:
        write_json(out/'summary.json',dict(passed=False,status='failed',error=str(exc)))
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--wheel',required=True,type=Path);p.add_argument('--dependency-dir',type=Path);a=p.parse_args()
    if a.dependency_dir:sys.path.insert(0,str(a.dependency_dir.resolve()))
    r=run(a.out,a.wheel);print('motulator steady:',r['points'],'points, passed:',r['passed'])
    if not r['passed']:raise SystemExit(1)
