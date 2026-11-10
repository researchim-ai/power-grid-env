"""Saturated/multicage PWM physics: coupled convergence and independent ODEs.

The full drive is checked by native replay and an independent mixed stage
solver. Its magnetic subsystem is additionally compared to a separate continuous
ODE over full prescribed-voltage trajectories; this is not an independent FOC
or complete switched-drive trajectory.
"""
import argparse,copy,hashlib,json,shutil
from pathlib import Path
import numpy as np
from benchmarks.switching_extended_cases import NAMES,document
from benchmarks.switching_drive_cases import document as baseline
from benchmarks.switching_drive_acceptance import LEVELS,comparison
from benchmarks.switching_stage_reference import solve_stage
from benchmarks.switching_winding_reference import reference
from power_grid_env.switching_drive import SwitchingDrive,SwitchingDriveStudy,run_switching_artifact,run_switching_drive
from power_grid_env.switching_magnetics import SwitchingMagnetics
from power_grid_env.study_artifacts import read_json,encoded,seal,verify
from power_grid_env.evidence import package_fingerprint
ROOT=Path(__file__).resolve().parents[1]
ALL_NAMES=(*NAMES,'baseline_foc')


def inputs(name):return document(name) if name in NAMES else baseline('foc_start')


def sources():
    files=['benchmarks/switching_extended_acceptance.py','benchmarks/switching_extended_cases.py','benchmarks/switching_drive_cases.py','benchmarks/switching_drive_acceptance.py','benchmarks/switching_stage_reference.py','benchmarks/switching_winding_reference.py','cases/studies/flux-saturated_dol.json']
    return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in files}


def stages():
    rows=[]
    for name in NAMES:
        for gate in [[[1,0],[0,1],[0,1]],[[0,0],[0,0],[0,0]]]:
            d=document(name);m=SwitchingDrive(SwitchingDriveStudy.model_validate(d));m.line=np.array([1.,-2.,1.]);m.speed=35.;state=dict(flux=m.flux.tolist(),line=m.line.tolist(),voltage=m.voltage.tolist(),speed=m.speed,temperature=m.temperature.tolist());g=np.array(gate,bool);q=m.candidate(.01,5e-6,g);ref=solve_stage(d,state,.01,5e-6,g,False)
            errors=dict(flux_vs=float(max(abs(q['flux_mid']-ref['flux_mid']))),line_a=float(max(abs(q['line_mid']-ref['line_mid']))),speed_rad_s=float(abs(q['speed_mid']-ref['speed_mid'])))
            rows.append(dict(case=name,gates=gate,reference=ref,errors=errors,passed=bool(errors['flux_vs']<1e-8 and errors['line_a']<1e-6 and errors['speed_rad_s']<1e-7)))
    return dict(rows=rows,passed=all(r['passed'] for r in rows))


def native_winding(passport,initial,speed,h):
    machine=SwitchingMagnetics(SwitchingDriveStudy.model_validate(passport).motor);x=np.array(initial);before=machine.energy(x);work=0.;out=0.;flux=[x.tolist()];errors=[];heat=0.
    for k in range(round(.01/h)):
        u=180*np.exp(1j*(2*np.pi*40*((k+.5)*h)+.2));v=np.array([u.real,u.imag]);guess=x.copy()
        for _ in range(10):
            a=machine.affine(x,h,speed,guess);delta=a['offset_delta']+a['gain_flux']@v;mid=x+delta;q=machine.stage(x,mid,h,v,delta)
            if q['constitutive_residual_v']<1e-9:break
            guess=mid
        else:raise ValueError('winding comparison magnetic iteration failed')
        work+=h*1.5*(v@q['currents'][:2]);out+=h*(q['copper_w']+q['core_w']+q['torque_nm']*speed);heat+=h*q['core_w'];x+=2*delta
        if (k+1)%round(.0001/h)==0:flux.append(x.tolist())
        errors.append(abs(work-out-(machine.energy(x)-before)))
    return dict(flux=flux,source_j=work,heat_and_shaft_j=out,core_heat_j=heat,energy_residual_j=max(errors))


def windings():
    times=np.linspace(0,.01,101).tolist();rows=[]
    for name in NAMES:
        d=document(name);p=d['motor'];initial=np.tile([.3,.1],1+len(p['cages']));ref=reference(p,initial,35.,times);runs={level:native_winding(d,initial,35.,h) for level,h in LEVELS};errors={k:float(max(abs(np.asarray(r['flux'])-np.asarray(ref['flux'])).ravel())) for k,r in runs.items()}
        heat_errors={k:abs(v['core_heat_j']-ref['core_heat_j'][-1]) for k,v in runs.items()};power_errors={k:abs(v['source_j']-ref['source_j'][-1]) for k,v in runs.items()}
        improved=errors['finest']<errors['fine']<errors['coarse'];passed=heat_errors['fine']<1e-4 and power_errors['fine']<1e-4 and improved and errors['fine']<2e-6 and max(r['energy_residual_j'] for r in runs.values())<1e-7
        rows.append(dict(case=name,reference=ref,native=runs,errors_vs=errors,core_heat_errors_j=heat_errors,source_errors_j=power_errors,passed=bool(passed)))
    return dict(rows=rows,passed=all(r['passed'] for r in rows),independent_continuous_subsystem_trajectories=len(NAMES))


def assess(name,d,results):
    fine=results['fine'];c=fine['channels'];metric=fine['metrics'];a=comparison(results['coarse'],results['finest']);b=comparison(fine,results['finest']);events=[[(e['kind'],e.get('component')) for e in r['event_log']] for r in results.values()]
    passed=fine['completed'] and metric['final_energy_relative_error']<1e-7 and metric['kcl_max_a']<5e-5 and metric['motor_constitutive_residual_v']<1e-8 and min(c['dc_voltage_v'])>=0 and b['maximum_scaled_error']<=1 and b['maximum_scaled_error']<=a['maximum_scaled_error'] and events[0]==events[1]==events[2]
    if name=='baseline_foc':passed=passed and abs(c['speed_rad_s'][-1]-30.)<1.5
    elif name=='saturated_regeneration':passed=passed and c['speed_rad_s'][-1]<175.
    else:passed=passed and c['speed_rad_s'][-1]>1.
    if d['motor'].get('core_loss_s',0.):passed=passed and metric['motor_core_heat_j']>0
    else:passed=passed and metric['motor_core_heat_j']==0
    if name=='saturated_block':passed=passed and any(v==0 for i,v in enumerate(c['gates_enabled']) if .0701<fine['time_s'][i]<.0799)
    return dict(case=name,coarse_to_finest=a,fine_to_finest=b,improved=bool(b['maximum_scaled_error']<=a['maximum_scaled_error']),final_speed_rad_s=c['speed_rad_s'][-1],metrics={k:r['metrics'] for k,r in results.items()},passed=bool(passed))


def qualify(out,reuse_from=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);producer=package_fingerprint();hashes=sources();reused=0;stage=stages();winding=windings();(out/'stages.json').write_bytes(encoded(stage));(out/'windings.json').write_bytes(encoded(winding));rows=[]
    for name in ALL_NAMES:
        d=inputs(name);results={}
        for level,h in LEVELS:
            p=copy.deepcopy(d);p['max_step_s']=h;target=out/name/level
            cached=Path(reuse_from)/name/level if reuse_from else None
            if cached is not None and (cached/'manifest.json').exists():
                verify(cached);r=read_json(cached/'result.json')
                if read_json(cached/'study.json')==p and r.get('code_sha256')==producer:
                    shutil.copytree(cached,target);results[level]=r;reused+=1;continue
            results[level]=run_switching_artifact(p,target)
        row=assess(name,d,results);rows.append(row);print(json.dumps(dict(case=name,passed=row['passed'],improved=row['improved'])),flush=True)
    summary=dict(schema='pge-switching-extended/v1',producer_sha256=producer,source_hashes=hashes,rows=rows,native_runs_reused=reused,independent_stage_cases=2*len(NAMES),independent_winding_trajectories=len(NAMES),independent_full_drive_trajectory=False,superiority_proven=False,manufacturer_accuracy_proven=False,passed=bool(stage['passed'] and winding['passed'] and all(r['passed'] for r in rows) and producer==package_fingerprint() and hashes==sources()))
    (out/'summary.json').write_bytes(encoded(summary));seal(out);return summary


def verify_qualification(out):
    out=Path(out);identity=verify(out);summary=read_json(out/'summary.json')
    if summary['schema']!='pge-switching-extended/v1' or summary['producer_sha256']!=package_fingerprint() or summary['source_hashes']!=sources():raise ValueError('current extended-switching producers required')
    if stages()!=read_json(out/'stages.json') or windings()!=read_json(out/'windings.json'):raise ValueError('independent magnetic replay differs')
    rows=[]
    for name in ALL_NAMES:
        d=inputs(name);results={}
        for level,h in LEVELS:
            folder=out/name/level;verify(folder);expected=copy.deepcopy(d);expected['max_step_s']=h
            if read_json(folder/'study.json')!=expected:raise ValueError('extended switching input differs')
            stored=read_json(folder/'result.json');replay=run_switching_drive(expected);replay['code_sha256']=package_fingerprint()
            if replay!=stored:raise ValueError('extended switching trajectory replay differs')
            results[level]=stored
        rows.append(assess(name,d,results));print(json.dumps(dict(replayed=name)),flush=True)
    if rows!=summary['rows'] or summary['passed']!=all(r['passed'] for r in rows) or any(summary[k] for k in ['independent_full_drive_trajectory','superiority_proven','manufacturer_accuracy_proven']):raise ValueError('extended switching conclusion differs')
    return dict(verified=True,passed=summary['passed'],manifest_sha256=identity,native_drive_trajectories_replayed=len(ALL_NAMES)*len(LEVELS),independent_winding_trajectories_replayed=len(NAMES),independent_stages_replayed=2*len(NAMES),independent_full_drive_trajectory=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser();g=parser.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');parser.add_argument('--reuse-from');a=parser.parse_args();r=qualify(a.out,a.reuse_from) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)
