"""Registered imposed-PWM motor comparison, not a full converter/FOC benchmark.

Production discrete-gradient port vs original pinned motulator Gamma RHS,
independent free-shaft equation and DOP853. Same event boundaries/inputs/axes.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import time
import zipfile
import numpy as np
from scipy.integrate import solve_ivp
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.prescribed_voltage import voltage_step
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify

VERSION='0.19.0'
WHEEL_SHA='f2aae0c1d0caf435f0bb9d7b87eda781bc4e4bc9fc7439122fb1b64fd42448eb'
NAMES=('linear_pwm','saturated_pwm','saturated_sag','regeneration')
LEVELS=(('coarse',2e-5),('fine',1e-5),('finest',5e-6))
LIMITS=dict(flux_vs=5e-5,current_a=.005,speed_rad_s=.005,torque_nm=.005)
CHANNELS=['stator_flux_alpha_vs','stator_flux_beta_vs','rotor_flux_alpha_vs','rotor_flux_beta_vs','current_alpha_a','current_beta_a','speed_rad_s','torque_nm']
SOURCE='https://aalto-electric-drives.github.io/motulator/drive_examples/current_vector/plot_2kw_im_sat_cvc.html'


def protocol(name):
    if name not in NAMES:raise ValueError('unknown registered PWM motor case')
    p=dict(id=name,node_id='motor',stator_r_ohm=3.7,unsaturated_h=.34,saturation_beta_per_vs=0. if name=='linear_pwm' else .84,saturation_exponent=7,cages=[dict(resistance_ohm=2.5,leakage_h=.023)],pole_pairs=2,inertia_kg_m2=.03,fan_nm_per_rad_s2=14.6/(math.pi*50)**2,initial_speed_rad_s=180. if name=='regeneration' else 0.,provenance=dict(electrical=SOURCE,magnetics=SOURCE,mechanical='authored inertia/fan benchmark, not identified OEM data'))
    return dict(schema='power-grid-env.pwm-motor-comparison/v1',row_channels=CHANNELS,case=name,motor=FluxMachinePassport.model_validate(p).model_dump(mode='json'),duration_s=.04,carrier_hz=2000.,fundamental_hz=30.,dc_v=540.,modulation=.8,zero_voltage_windows=[[.015,.02]] if name=='saturated_sag' else [],sample_s=.0002,levels=dict(LEVELS),limits=LIMITS,reference=dict(package='motulator',version=VERSION,wheel_sha256=WHEEL_SHA,method='DOP853',rtol=1e-11,atol=1e-12,max_step_s=5e-5),scope='one single-cage Gamma motor, prescribed ideal two-level terminal voltage, free shaft; no inverter diode network, dead time, source RL/DC dynamics, controller or thermal model')


def waveform(spec):
    """Independent ideal center-aligned PWM fixture; not runtime PWM planner."""
    period=1/spec['carrier_hz'];segments=[]
    for k in range(round(spec['duration_s']/period)):
        start=k*period
        angle=2*math.pi*spec['fundamental_hz']*(start+period/2)
        duty=.5+.5*spec['modulation']*np.cos(angle-np.arange(3)*2*math.pi/3)
        on=start+(1-duty)*period/2;off=start+(1+duty)*period/2
        points={start,(k+1)*period,*on,*off}
        points.update(t for pair in spec['zero_voltage_windows'] for t in pair if start<t<(k+1)*period)
        points=sorted(points)
        for left,right in zip(points,points[1:]):
            middle=(left+right)/2
            gates=((middle>=on)&(middle<off)).astype(float)
            voltage=2/3*spec['dc_v']*np.sum(gates*np.exp(2j*math.pi*np.arange(3)/3))
            if any(a<=middle<b for a,b in spec['zero_voltage_windows']):voltage=0j
            segments.append((float(left),float(right),complex(voltage)))
    return segments


def energy(p,flux,speed):
    # Diagnostic independently expressed from public Gamma energy law.
    z=np.asarray(flux).reshape(2,2);s=complex(*z[0]);r=complex(*z[1]);radius=abs(s)
    return 1.5/p['unsaturated_h']*(radius**2/2+p['saturation_beta_per_vs']**p['saturation_exponent']*radius**(p['saturation_exponent']+2)/(p['saturation_exponent']+2))+.75*abs(r-s)**2/p['cages'][0]['leakage_h']+.5*p['inertia_kg_m2']*speed**2


def quantities(p,flux,speed):
    z=np.asarray(flux).reshape(2,2);s=complex(*z[0]);r=complex(*z[1]);ir=(r-s)/p['cages'][0]['leakage_h'];is_=s*(1+(p['saturation_beta_per_vs']*abs(s))**p['saturation_exponent'])/p['unsaturated_h']-ir
    torque=1.5*p['pole_pairs']*(is_*np.conj(s)).imag
    return np.r_[z.ravel(),is_.real,is_.imag,speed,torque]


def native(spec,step):
    p=spec['motor'];passport=FluxMachinePassport.model_validate(p);flux=np.zeros(4);speed=p['initial_speed_rad_s'];work=loss=0.;initial=energy(p,flux,speed);peak_residual=0.;count=0
    times=np.arange(round(spec['duration_s']/spec['sample_s'])+1)*spec['sample_s'];rows=[quantities(p,flux,speed).tolist()];sample=1
    for left,right,voltage in waveform(spec):
        t=left
        while t<right-1e-14:
            h=min(step,right-t,times[sample]-t if sample<len(times) else right-t)
            q=voltage_step(passport,flux,speed,voltage,h)
            flux=q['flux'];speed=q['speed_rad_s']
            work+=h*q['source_w'];loss+=h*(q['stator_copper_w']+q['rotor_copper_w']+q['core_loss_w']+q['load_nm']*q['speed_mid_rad_s'])
            peak_residual=max(peak_residual,abs(work-loss-(energy(p,flux,speed)-initial)));count+=1;t+=h
            if sample<len(times) and abs(t-times[sample])<1e-13:rows.append(quantities(p,flux,speed).tolist());sample+=1
    if len(rows)!=len(times):raise ValueError('native sample inventory differs')
    return dict(row_channels=CHANNELS,time_s=times.tolist(),rows=rows,steps=count,source_j=work,heat_and_load_j=loss,maximum_energy_residual_j=peak_residual)


def authenticate(wheel,version=VERSION,wheel_sha=WHEEL_SHA):
    wheel=Path(wheel)
    if hashlib.sha256(wheel.read_bytes()).hexdigest()!=wheel_sha:raise ValueError('pinned wheel SHA256 mismatch')
    if importlib.metadata.version('motulator')!=version:raise ValueError('motulator version differs')
    import motulator
    root=Path(motulator.__file__).parent;hashes={}
    with zipfile.ZipFile(wheel) as archive:
        for path in sorted(root.rglob('*.py')):
            key='motulator/'+path.relative_to(root).as_posix();raw=path.read_bytes()
            if raw!=archive.read(key):raise ValueError('installed source differs: '+key)
            hashes[key]=hashlib.sha256(raw).hexdigest()
        expected={n for n in archive.namelist() if n.startswith('motulator/') and n.endswith('.py')}
        if set(hashes)!=expected:raise ValueError('installed source inventory differs')
    return hashes


def external(spec,*,rtol=1e-11,atol=1e-12,max_step=5e-5):
    from motulator.drive.model import InductionMachine,InductionMachinePars
    p=spec['motor'];c=p['cages'][0]
    machine=InductionMachine(InductionMachinePars(n_p=p['pole_pairs'],R_s=p['stator_r_ohm'],R_r=c['resistance_ohm'],L_ell=c['leakage_h'],L_s=lambda rho:p['unsaturated_h']/(1+(p['saturation_beta_per_vs']*rho)**p['saturation_exponent'])))
    initial=np.r_[np.zeros(4),p['initial_speed_rad_s'],0.,0.];state=initial.copy();initial_e=energy(p,state[:4],state[4]);peak_residual=0.;evaluations=0
    times=np.arange(round(spec['duration_s']/spec['sample_s'])+1)*spec['sample_s'];rows=[];cursor=0
    def output(t,x,voltage):
        machine.state.psi_s_ab=complex(*x[:2]);machine.state.psi_r_ab=complex(*x[2:4]);machine.inp.w_M=x[4];machine.inp.u_s_ab=voltage;machine.set_outputs(t)
        current,rotor,torque=machine.out.i_s_ab,machine.out.i_r_ab,machine.out.tau_M
        return current,rotor,float(torque)
    for left,right,voltage in waveform(spec):
        def rhs(t,x):
            current,rotor,torque=output(t,x,voltage);ds,dr=machine.rhs(t)
            load=p['fan_nm_per_rad_s2']*x[4]*abs(x[4])+p['viscous_nm_per_rad_s']*x[4]
            return [ds.real,ds.imag,dr.real,dr.imag,(torque-load)/p['inertia_kg_m2'],1.5*(voltage*np.conj(current)).real,1.5*(p['stator_r_ohm']*abs(current)**2+c['resistance_ohm']*abs(rotor)**2)+load*x[4]]
        sol=solve_ivp(rhs,(left,right),state,method='DOP853',rtol=rtol,atol=atol,max_step=max_step,dense_output=True)
        if not sol.success:raise RuntimeError('motulator trajectory failed')
        evaluations+=sol.nfev
        for t,x in zip(sol.t,sol.y.T):peak_residual=max(peak_residual,abs(x[5]-x[6]-(energy(p,x[:4],x[4])-initial_e)))
        while cursor<len(times) and times[cursor]<=right+1e-14:
            t=times[cursor];x=sol.sol(min(right,max(left,t)));current,_,torque=output(t,x,voltage)
            rows.append([*x[:4],current.real,current.imag,x[4],torque]);cursor+=1
        state=sol.y[:,-1]
    return dict(row_channels=CHANNELS,time_s=times.tolist(),rows=np.asarray(rows).tolist(),rhs_evaluations=evaluations,source_j=float(state[5]),heat_and_load_j=float(state[6]),maximum_energy_residual_j=peak_residual)


def compare(primary,reference,spec=None):
    if spec is not None:
        expected=(np.arange(round(spec['duration_s']/spec['sample_s'])+1)*spec['sample_s']).tolist()
        if primary['time_s']!=expected or reference['time_s']!=expected:raise ValueError('registered horizon/sample inventory differs')
        if primary.get('row_channels')!=spec['row_channels'] or reference.get('row_channels')!=spec['row_channels']:raise ValueError('registered channel inventory differs')
    if primary['time_s']!=reference['time_s']:raise ValueError('comparison axes differ')
    a=np.asarray(primary['rows']);b=np.asarray(reference['rows'])
    if a.ndim!=2 or b.ndim!=2 or a.shape!=b.shape or a.shape[1]!=8 or not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):raise ValueError('comparison inventory differs')
    d=abs(a-b);errors=dict(flux_vs=float(d[:,:4].max()),current_a=float(d[:,4:6].max()),speed_rad_s=float(d[:,6].max()),torque_nm=float(d[:,7].max()))
    return dict(errors=errors,passed=all(errors[k]<LIMITS[k] for k in LIMITS))


def producers(wheel):
    return dict(package=package_fingerprint(),benchmark=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),external_sources=authenticate(wheel),reference_version=VERSION)


def run(out,wheel,replay=False):
    out=Path(out);before=producers(wheel)
    if replay:
        manifest=verify(out)
        if read_json(out/'producer.json')!=before:raise ValueError('producer differs')
    else:
        out.mkdir(parents=True,exist_ok=False);(out/'producer.json').write_bytes(encoded(before))
    def retain(name,value):
        if replay:
            if encoded(read_json(out/name))!=encoded(value):raise ValueError('replay differs: '+name)
        else:(out/name).write_bytes(encoded(value))
    # Import both native providers before clocks; do not charge the first
    # reference trajectory for lazy module imports. Construction stays timed.
    from motulator.drive.model import InductionMachine,InductionMachinePars
    rows=[];timing={}
    for name in NAMES:
        spec=protocol(name);retain(name+'-protocol.json',spec)
        start=time.perf_counter();ref=external(spec);reference_time=time.perf_counter()-start;retain(name+'-motulator.json',ref)
        runs={};comparisons={};elapsed={}
        for level,step in LEVELS:
            start=time.perf_counter();runs[level]=native(spec,step);elapsed[level]=time.perf_counter()-start
            retain(name+'-'+level+'.json',runs[level]);comparisons[level]=compare(runs[level],ref,spec)
        improved=all(comparisons['finest']['errors'][k]<comparisons['fine']['errors'][k]<comparisons['coarse']['errors'][k] for k in LIMITS)
        sampled_power=[]
        fixture=waveform(spec)
        for t,r in zip(ref['time_s'],ref['rows']):
            u=next((v for a,b,v in fixture if a<=t<b),fixture[-1][2])
            sampled_power.append(float(1.5*(u*np.conj(complex(r[4],r[5]))).real))
        regenerative_interval=(min(sampled_power)<-1. and min(r[7] for r in ref['rows'])<-.01) if name=='regeneration' else True
        passed=bool(regenerative_interval and comparisons['fine']['passed'] and comparisons['finest']['passed'] and improved and max(r['maximum_energy_residual_j'] for r in runs.values())<1e-6)
        row=dict(case=name,passed=passed,minimum_sampled_reference_source_w=min(sampled_power),comparisons=comparisons,convergence_all_channels=improved,native_energy_residual_j=runs['fine']['maximum_energy_residual_j'],reference_energy_residual_j=ref['maximum_energy_residual_j']);rows.append(row)
        timing[name]=dict(native_wall_s=elapsed,reference_wall_s=reference_time,scope='single sequential local measurements; unequal solvers/error controls; no speed superiority claim')
        print(encoded(row).decode(),flush=True)
    summary=dict(schema='power-grid-env.pwm-motor-comparison-result/v1',passed=all(r['passed'] for r in rows),rows=rows,external_cases=4,native_trajectories=12,full_closed_loop_drive_compared=False,superiority_established=False,scope='matched imposed-PWM single-cage winding+shaft trajectories; no controller, converter or source-network comparison')
    retain('summary.json',summary)
    if not replay:
        (out/'timing.json').write_bytes(encoded(dict(environment=dict(python=platform.python_version(),platform=platform.platform(),numpy=np.__version__),measurements=timing)))
    if producers(wheel)!=before:raise ValueError('producer changed during run')
    identity=verify(out) if replay else seal(out)
    if replay and manifest!=identity:raise ValueError('artifact changed during replay')
    print(encoded(dict(passed=summary['passed'],replayed=replay,manifest_sha256=identity)).decode(),flush=True)
    if not summary['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--out');group.add_argument('--verify');parser.add_argument('--wheel',required=True);args=parser.parse_args();run(args.verify or args.out,args.wheel,bool(args.verify))
