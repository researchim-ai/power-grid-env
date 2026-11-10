"""Electrothermal PWM qualification: independent ODE and mixed-stage oracles."""
import argparse,copy,hashlib,json,shutil
from pathlib import Path
import numpy as np
from scipy.linalg import expm
from benchmarks.switching_thermal_cases import NAMES,document
from benchmarks.switching_extended_cases import document as previous
from benchmarks.switching_drive_acceptance import LEVELS
from benchmarks.switching_stage_reference import solve_stage
from benchmarks.switching_thermal_reference import reference
from power_grid_env.switching_drive import SwitchingDrive,SwitchingDriveStudy,run_switching_artifact,run_switching_drive
from power_grid_env.switching_magnetics import SwitchingMagnetics
from power_grid_env.switching_winding_thermal import winding_thermal_step
from power_grid_env.study_artifacts import read_json,encoded,seal,verify
from power_grid_env.evidence import package_fingerprint
ROOT=Path(__file__).resolve().parents[1]
ALL_NAMES=(*NAMES,'nonthermal_baseline')


def inputs(name):return document(name) if name in NAMES else previous('core_single')


def sources():
    files=['benchmarks/switching_thermal_acceptance.py','benchmarks/switching_thermal_cases.py','benchmarks/switching_thermal_reference.py','benchmarks/switching_stage_reference.py','benchmarks/switching_extended_cases.py','benchmarks/switching_drive_cases.py','benchmarks/switching_drive_acceptance.py','cases/studies/flux-saturated_dol.json']
    return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in files}


def stages():
    rows=[]
    for name in NAMES:
        for gate in [[[1,0],[0,1],[0,1]],[[0,0],[0,0],[0,0]]]:
            d=document(name);m=SwitchingDrive(SwitchingDriveStudy.model_validate(d));m.line=np.array([1.,-2.,1.]);m.speed=35.;state=dict(flux=m.flux.tolist(),line=m.line.tolist(),voltage=m.voltage.tolist(),speed=m.speed,temperature=m.temperature.tolist(),winding_temperature=m.winding_temperature.tolist());g=np.array(gate,bool);q=m.candidate(.01,1e-5,g);ref=solve_stage(d,state,.01,1e-5,g,False)
            errors=dict(flux_vs=float(max(abs(q['flux_mid']-ref['flux_mid']))),line_a=float(max(abs(q['line_mid']-ref['line_mid']))),temperature_c=float(max(abs(q['winding_temperature']-(2*np.array(ref['winding_temperature_mid_c'])-m.winding_temperature)))))
            rows.append(dict(case=name,gates=gate,reference=ref,errors=errors,passed=bool(errors['flux_vs']<1e-8 and errors['line_a']<1e-6 and errors['temperature_c']<1e-8)))
    return dict(rows=rows,passed=all(r['passed'] for r in rows))


def native_winding(d,initial,initial_t,speed,step):
    p=SwitchingDriveStudy.model_validate(d);machine=SwitchingMagnetics(p.motor);th=p.motor.thermal;C=np.array([th.stator_capacity_j_per_k,th.rotor_capacity_j_per_k]);x=np.array(initial);temperature=np.array(initial_t);before=machine.energy(x)+C@(temperature-th.ambient_c)
    work=0.;out=0.;qs=0.;qr=0.;errors=[];flux=[x.tolist()];temps=[temperature.tolist()]
    for k in range(round(.01/step)):
        u=180*np.exp(1j*(2*np.pi*40*((k+.5)*step)+.2));v=np.array([u.real,u.imag]);guess=x.copy();tmid=temperature.copy()
        for _ in range(16):
            a=machine.affine(x,step,speed,guess,tmid);delta=a['offset_delta']+a['gain_flux']@v;mid=x+delta;q=machine.stage(x,mid,step,v,delta,tmid)
            next_t,ambient=winding_thermal_step(th,temperature,[q['stator_copper_w']+q['core_w'],q['rotor_copper_w']],step);next_mid=(temperature+next_t)/2;err=max(abs(next_mid-tmid));tmid=next_mid
            if q['constitutive_residual_v']<1e-9 and err<1e-10:break
            guess=mid
        else:raise ValueError('electrothermal winding comparison did not converge')
        work+=step*1.5*(v@q['currents'][:2]);out+=sum(ambient)+step*q['torque_nm']*speed;qs+=step*(q['stator_copper_w']+q['core_w']);qr+=step*q['rotor_copper_w'];x+=2*delta;temperature=next_t
        if (k+1)%round(.0001/step)==0:flux.append(x.tolist());temps.append(temperature.tolist())
        errors.append(abs(work-out-(machine.energy(x)+C@(temperature-th.ambient_c)-before)))
    return dict(flux=flux,temperature_c=temps,source_j=work,ambient_and_shaft_j=out,stator_heat_j=qs,rotor_heat_j=qr,energy_residual_j=max(errors))


def windings():
    times=np.linspace(0,.01,101).tolist();rows=[]
    for name in NAMES:
        d=document(name);p=d['motor'];initial=np.tile([.3,.1],1+len(p['cages']));initial_t=d.get('motor_initial_temperature_c') or [p['thermal']['ambient_c']]*2
        ref=reference(p,initial,initial_t,35.,times);runs={level:native_winding(d,initial,initial_t,35.,h) for level,h in LEVELS}
        errors={k:dict(flux_vs=float(max(abs(np.asarray(r['flux'])-np.asarray(ref['flux'])).ravel())),temperature_c=float(max(abs(np.asarray(r['temperature_c'])-np.asarray(ref['temperature_c'])).ravel())),source_j=abs(r['source_j']-ref['source_j'][-1]),stator_heat_j=abs(r['stator_heat_j']-ref['stator_heat_j'][-1]),rotor_heat_j=abs(r['rotor_heat_j']-ref['rotor_heat_j'][-1])) for k,r in runs.items()}
        fine=errors['fine'];improved=errors['finest']['flux_vs']<fine['flux_vs']<errors['coarse']['flux_vs']
        passed=improved and fine['flux_vs']<2e-6 and fine['temperature_c']<1e-5 and max(fine[k] for k in ['source_j','stator_heat_j','rotor_heat_j'])<1e-4 and max(r['energy_residual_j'] for r in runs.values())<1e-7
        rows.append(dict(case=name,reference=ref,native=runs,errors=errors,passed=bool(passed)))
    return dict(rows=rows,passed=all(r['passed'] for r in rows))


def comparison(a,b):
    if len(a['time_s'])!=len(b['time_s']) or not np.allclose(a['time_s'],b['time_s'],rtol=0,atol=5e-13):raise ValueError('electrothermal sample grid differs')
    limits={'dc_voltage_v':.1,'speed_rad_s':.05,'rectifier_temperature_c':.01,'inverter_temperature_c':.01,'brake_temperature_c':.01,'flux_estimate_vs':.001}
    if 'motor_stator_temperature_c' in a['channels']:limits.update(motor_stator_temperature_c=.001,motor_rotor_temperature_c=.001,motor_stator_resistance_ohm=1e-5)
    errors={};scaled=[]
    for key,limit in limits.items():
        x=np.array(a['channels'][key]);y=np.array(b['channels'][key]);errors[key]=float(max(abs(x-y)));scaled.append(errors[key]/(limit+1e-3*max(abs(y))))
    return dict(errors=errors,maximum_scaled_error=float(max(scaled)))


def features(name,d,r):
    c=r['channels'];metric=r['metrics'];events=r['event_log'];required=r['completed'] and metric['final_energy_relative_error']<1e-7 and metric['kcl_max_a']<5e-5 and metric['motor_constitutive_residual_v']<1e-8
    observed=dict(final_speed_rad_s=c['speed_rad_s'][-1],thermal_events=[e for e in events if e['kind']=='thermal_trip'])
    if name=='nonthermal_baseline':return dict(observed,passed=bool(required and c['speed_rad_s'][-1]>1.))
    h=d['motor']['thermal'];initial=d.get('motor_initial_temperature_c') or [h['ambient_c']]*2;final=np.array([c['motor_stator_temperature_c'][-1],c['motor_rotor_temperature_c'][-1]]);C=np.array([h['stator_capacity_j_per_k'],h['rotor_capacity_j_per_k']])
    heat_error=abs(C@(final-initial)-(metric['motor_stator_heat_j']+metric['motor_rotor_heat_j']-metric['motor_winding_ambient_heat_j']));observed.update(final_winding_temperature_c=final.tolist(),winding_heat_balance_error_j=float(heat_error))
    required=required and heat_error<1e-6 and min(c['motor_stator_temperature_c'])>=h['ambient_c']-1e-8 and min(c['motor_rotor_temperature_c'])>=h['ambient_c']-1e-8 and max(final)<h['maximum_c']
    if name in {'cold_winding','warm_winding','saturated_four_thermal'}:required=required and c['speed_rad_s'][-1]>1 and not observed['thermal_events']
    if name=='warm_winding':required=required and c['motor_stator_resistance_ohm'][-1]>1.2*d['motor']['stator_r_ohm']
    for prefix in ['stator','rotor']:
        if name==prefix+'_trip':required=required and any(e['component']=='motor_'+prefix for e in observed['thermal_events']) and c['gates_enabled'][-1]==0
    if name in {'initial_trip_cooling','winding_cooling'}:
        H=np.diag([h['stator_ambient_w_per_k'],h['rotor_ambient_w_per_k']])+h['cross_w_per_k']*np.array([[1.,-1.],[-1.,1.]])
        exact=h['ambient_c']+expm(-np.linalg.solve(np.diag(C),H)*d['duration_s'])@(np.array(initial)-h['ambient_c']);error=float(max(abs(final-exact)));observed['exact_cooling_error_c']=error
        required=required and metric['gate_edges']==0 and error<1e-6 and final[0]<initial[0] and final[1]<initial[1]
    if name=='initial_trip_cooling':required=required and r['initial_winding_thermal_trips']==[True,False] and c['motor_stator_thermal_trip'][-1]==1
    return dict(observed,passed=bool(required))


def assess(name,d,results):
    fine=results['fine'];a=comparison(results['coarse'],results['finest']);b=comparison(fine,results['finest']);f=features(name,d,fine);seq=[[(e['kind'],e.get('component')) for e in r['event_log']] for r in results.values()];same=seq[0]==seq[1]==seq[2];event_error=max([0.,*[abs(x['time_s']-y['time_s']) for x,y in zip(fine['event_log'],results['finest']['event_log'])]])
    return dict(case=name,coarse_to_finest=a,fine_to_finest=b,features=f,event_sequence_preserved=same,event_time_error_s=event_error,metrics={k:r['metrics'] for k,r in results.items()},improved=bool(b['maximum_scaled_error']<=a['maximum_scaled_error']),passed=bool(f['passed'] and same and event_error<=2/d['pwm_frequency_hz'] and b['maximum_scaled_error']<=1 and b['maximum_scaled_error']<=a['maximum_scaled_error']))


def qualify(out,reuse_from=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);producer=package_fingerprint();hashes=sources();reused=0;stage=stages();winding=windings();(out/'stages.json').write_bytes(encoded(stage));(out/'windings.json').write_bytes(encoded(winding));rows=[]
    for name in ALL_NAMES:
        d=inputs(name);results={}
        for level,step in LEVELS:
            p=copy.deepcopy(d);p['max_step_s']=step;target=out/name/level;cached=Path(reuse_from)/name/level if reuse_from else None
            if cached is not None and (cached/'manifest.json').exists():
                verify(cached);r=read_json(cached/'result.json')
                if read_json(cached/'study.json')==p and r.get('code_sha256')==producer:shutil.copytree(cached,target);results[level]=r;reused+=1;continue
            results[level]=run_switching_artifact(p,target)
        row=assess(name,d,results);rows.append(row);print(json.dumps(dict(case=name,passed=row['passed'],improved=row['improved'])),flush=True)
    summary=dict(schema='pge-switching-winding-thermal/v1',producer_sha256=producer,source_hashes=hashes,rows=rows,native_runs_reused=reused,independent_stage_cases=2*len(NAMES),independent_electrothermal_winding_cases=len(NAMES),independent_full_drive_trajectory=False,superiority_proven=False,manufacturer_accuracy_proven=False,passed=bool(stage['passed'] and winding['passed'] and all(r['passed'] for r in rows) and producer==package_fingerprint() and hashes==sources()))
    (out/'summary.json').write_bytes(encoded(summary));seal(out);return summary


def verify_qualification(out):
    out=Path(out);identity=verify(out);summary=read_json(out/'summary.json')
    if summary['schema']!='pge-switching-winding-thermal/v1' or summary['producer_sha256']!=package_fingerprint() or summary['source_hashes']!=sources():raise ValueError('current winding-thermal producers required')
    if stages()!=read_json(out/'stages.json') or windings()!=read_json(out/'windings.json'):raise ValueError('independent electrothermal replay differs')
    rows=[]
    for name in ALL_NAMES:
        d=inputs(name);results={}
        for level,step in LEVELS:
            folder=out/name/level;verify(folder);expected=copy.deepcopy(d);expected['max_step_s']=step
            if read_json(folder/'study.json')!=expected:raise ValueError('winding-thermal input differs')
            stored=read_json(folder/'result.json');replay=run_switching_drive(expected);replay['code_sha256']=package_fingerprint()
            if replay!=stored:raise ValueError('electrothermal drive replay differs')
            results[level]=stored
        rows.append(assess(name,d,results));print(json.dumps(dict(replayed=name)),flush=True)
    if rows!=summary['rows'] or summary['passed']!=all(r['passed'] for r in rows) or summary['independent_stage_cases']!=2*len(NAMES) or summary['independent_electrothermal_winding_cases']!=len(NAMES) or any(summary[k] for k in ['independent_full_drive_trajectory','superiority_proven','manufacturer_accuracy_proven']):raise ValueError('winding-thermal conclusion differs')
    return dict(verified=True,passed=summary['passed'],manifest_sha256=identity,native_drive_trajectories_replayed=len(ALL_NAMES)*len(LEVELS),independent_electrothermal_winding_cases_replayed=len(NAMES),independent_stages_replayed=2*len(NAMES),independent_full_drive_trajectory=False)


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');p.add_argument('--reuse-from');a=p.parse_args()
    if a.verify and a.reuse_from:p.error('--reuse-from requires --out')
    r=qualify(a.out,a.reuse_from) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)
