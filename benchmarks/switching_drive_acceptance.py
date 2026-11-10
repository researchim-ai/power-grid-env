"""Bounded PWM/FOC qualification: 3-step convergence + independent stage oracles.

Verification replays all full native trajectories and independent stage solves.
There is no independent full closed-loop trajectory, hardware or OEM claim.
"""
import argparse,copy,hashlib,json,shutil
from pathlib import Path
import numpy as np
from benchmarks.switching_drive_cases import NAMES,document
from benchmarks.switching_stage_reference import solve_stage
from power_grid_env.switching_drive import SwitchingDrive,SwitchingDriveStudy,run_switching_artifact,run_switching_drive
from power_grid_env.study_artifacts import read_json,encoded,seal,verify
from power_grid_env.evidence import package_fingerprint
ROOT=Path(__file__).resolve().parents[1]
LEVELS=(('coarse',2e-5),('fine',1e-5),('finest',5e-6))
CHANNELS={'dc_voltage_v':.1,'speed_rad_s':.05,'rectifier_temperature_c':.01,'inverter_temperature_c':.01,'brake_temperature_c':.01,'flux_estimate_vs':.001}


def sources():
    names=['benchmarks/switching_drive_acceptance.py','benchmarks/switching_drive_cases.py','benchmarks/switching_stage_reference.py','cases/studies/flux-saturated_dol.json']
    return {s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in names}


def stage_inputs():
    rng=np.random.default_rng(6034);rows=[]
    for i in range(12):
        p=document('foc_start');state=dict(flux=rng.normal(0,.07,4).tolist(),line=rng.normal(0,2.,3).tolist(),voltage=[0.,0.,0.,350. if i%2 else 270.,-350. if i%2 else -270.,0.,0.,0.],speed=float([-30.,0.,60.][i%3]),temperature=[30.,40.,35.])
        gates=[[[1,0],[0,1],[0,1]],[[0,0],[0,1],[1,0]],[[0,0],[0,0],[0,0]]][i%3]
        rows.append(dict(parameters=p,state=state,t=.007+i*.0004,h=4e-6 if i%2 else 1e-5,gates=gates,bypassed=bool(i%2)))
    return rows


def stage_assessment():
    rows=[]
    for inputs in stage_inputs():
        p=inputs['parameters'];state=inputs['state'];gates=np.array(inputs['gates'],bool);m=SwitchingDrive(SwitchingDriveStudy.model_validate(p));m.flux=np.array(state['flux']);m.line=np.array(state['line']);m.voltage=np.array(state['voltage']);m.speed=state['speed'];m.temperature=np.array(state['temperature']);m.bypassed=inputs['bypassed']
        q=m.candidate(inputs['t'],inputs['h'],gates);ref=solve_stage(p,state,inputs['t'],inputs['h'],gates,inputs['bypassed'])
        errors=dict(line_mid=float(max(abs(q['line_mid']-ref['line_mid']))),flux_mid=float(max(abs(q['flux_mid']-ref['flux_mid']))),speed_mid=abs(q['speed_mid']-ref['speed_mid']),motor_line_voltage=float(max(abs(np.diff(q['voltage'][5:])-np.diff(ref['voltage'][5:])))))
        passed=errors['line_mid']<1e-6 and errors['flux_mid']<1e-8 and errors['speed_mid']<1e-7 and errors['motor_line_voltage']<.001 and ref['residual_max']<2e-6
        rows.append(dict(inputs=inputs,reference=ref,errors=errors,passed=bool(passed)))
    return dict(rows=rows,passed=all(r['passed'] for r in rows),scope='independent simultaneous 16-unknown midpoint stages; no independent full trajectory')


def comparison(a,b):
    if len(a['time_s'])!=len(b['time_s']) or not np.allclose(a['time_s'],b['time_s'],rtol=0.,atol=5e-13):raise ValueError('registered sample inventory differs')
    errors={};scaled=[]
    for key,absolute in CHANNELS.items():
        x=np.array(a['channels'][key]);y=np.array(b['channels'][key]);error=float(max(abs(x-y)))
        errors[key]=error;scaled.append(error/(absolute+1e-3*max(abs(y))))
    return dict(errors=errors,maximum_scaled_error=float(max(scaled)))


def features(name,p,r):
    c=r['channels'];events=r['event_log'];metric=r['metrics'];threshold=any(e['kind']=='thermal_trip' for e in events)
    required=r['completed'] and metric['final_energy_relative_error']<1e-7 and metric['kcl_max_a']<5e-5 and min(c['dc_voltage_v'])>=0 and max(c['current_reference_peak_a'])<=p['control']['current_limit_peak_a']+1e-10
    observed=dict(final_speed_rad_s=c['speed_rad_s'][-1],peak_dc_voltage_v=max(c['dc_voltage_v']),peak_inverter_temperature_c=max(c['inverter_temperature_c']),thermal_trip=threshold,body_diode_heat_j=metric['body_diode_heat_j'],inductive_overlap_steps=metric['inductive_overlap_steps'])
    if name=='foc_start':required=required and abs(c['speed_rad_s'][-1]-p['control']['speed_reference_rad_s'])<max(1.,.05*abs(p['control']['speed_reference_rad_s'])) and not threshold and metric['inductive_overlap_steps']>0
    if name=='pwm_block_recover':
        inside=[i for i,t in enumerate(r['time_s']) if .0301<t<.0399]
        required=required and bool(inside) and all(c['gates_enabled'][i]==0 for i in inside) and metric['body_diode_heat_j']>0 and c['speed_rad_s'][-1]>5.
    if name=='grid_sag':required=required and c['speed_rad_s'][-1]>5 and not threshold
    for component in ['inverter','rectifier','brake']:
        if name==component+'_thermal_trip':required=required and any(e['kind']=='thermal_trip' and e['component']==component for e in events) and c['thermal_trip'][-1]==1 and c['gates_enabled'][-1]==0
    if name=='rectifier_thermal_trip':required=required and not any(e['kind']=='precharge_bypass' for e in events)
    if name=='regeneration':required=required and c['speed_rad_s'][-1]<p['motor']['initial_speed_rad_s']-5 and max(c['dc_voltage_v'])>p['brake_start_v'] and metric['switching_heat_j']>0
    return dict(observed,passed=bool(required))


def row_result(name,p,results):
    coarse,fine,finest=[results[k] for k,_ in LEVELS]
    a=comparison(coarse,finest);b=comparison(fine,finest);f=features(name,p,fine)
    event_sequences=[[(e['kind'],e.get('component')) for e in r['event_log']] for r in [coarse,fine,finest]]
    same_events=event_sequences[0]==event_sequences[1]==event_sequences[2]
    event_error=max([0.,*[abs(x['time_s']-y['time_s']) for x,y in zip(fine['event_log'],finest['event_log'])]])
    improved=bool(b['maximum_scaled_error']<=a['maximum_scaled_error'])
    return dict(case=name,coarse_to_finest=a,fine_to_finest=b,improved=improved,event_sequence_preserved=same_events,event_time_error_s=event_error,features=f,resources={k:r['metrics'] for k,r in results.items()},passed=bool(f['passed'] and b['maximum_scaled_error']<=1 and improved and same_events and event_error<=2/p['pwm_frequency_hz']))


def qualify(out,reuse_from=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);producer=package_fingerprint();hashes=sources();rows=[];reused=0
    stage=stage_assessment();(out/'stage-oracles.json').write_bytes(encoded(stage))
    for name in NAMES:
        p=document(name);results={}
        for level,h in LEVELS:
            d=copy.deepcopy(p);d['max_step_s']=h;target=out/name/level
            cached=Path(reuse_from)/name/level if reuse_from else None
            if cached is not None and (cached/'manifest.json').exists():
                verify(cached)
                cached_result=read_json(cached/'result.json')
                if read_json(cached/'study.json')==d and cached_result.get('code_sha256')==producer:
                    shutil.copytree(cached,target);results[level]=cached_result;reused+=1;continue
            results[level]=run_switching_artifact(d,target)
        row=row_result(name,p,results);rows.append(row);print(json.dumps(dict(case=name,passed=row['passed'],improved=row['improved'])),flush=True)
    summary=dict(schema='pge-switching-drive-qualification/v1',producer_sha256=producer,source_hashes=hashes,rows=rows,native_runs_reused=reused,independent_stages=12,independent_full_trajectory=False,manufacturer_accuracy_proven=False,superiority_proven=False,passed=bool(stage['passed'] and all(r['passed'] for r in rows) and producer==package_fingerprint() and hashes==sources()))
    (out/'summary.json').write_bytes(encoded(summary));seal(out);return summary


def verify_qualification(out):
    out=Path(out);identity=verify(out);s=read_json(out/'summary.json')
    if s['schema']!='pge-switching-drive-qualification/v1' or s['producer_sha256']!=package_fingerprint() or s['source_hashes']!=sources():raise ValueError('current switching producers required')
    stage=stage_assessment()
    if stage!=read_json(out/'stage-oracles.json') or not stage['passed']:raise ValueError('independent stage replay differs')
    rows=[]
    for name in NAMES:
        p=document(name);results={}
        for level,h in LEVELS:
            folder=out/name/level;verify(folder);expected=copy.deepcopy(p);expected['max_step_s']=h
            if read_json(folder/'study.json')!=expected:raise ValueError('switching study differs')
            stored=read_json(folder/'result.json');replayed=run_switching_drive(expected);replayed['code_sha256']=package_fingerprint()
            if stored!=replayed:raise ValueError('full switching trajectory replay differs')
            results[level]=stored
        rows.append(row_result(name,p,results));print(json.dumps(dict(replayed=name)),flush=True)
    if rows!=s['rows'] or s['passed']!=all(r['passed'] for r in rows) or s['independent_stages']!=12 or any(s[k] for k in ['independent_full_trajectory','manufacturer_accuracy_proven','superiority_proven']):raise ValueError('switching conclusions differ')
    return dict(verified=True,passed=s['passed'],manifest_sha256=identity,native_trajectories_replayed=len(NAMES)*len(LEVELS),independent_stages_replayed=12,independent_full_trajectory=False)


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');p.add_argument('--reuse-from');a=p.parse_args()
    if a.verify and a.reuse_from:p.error('--reuse-from requires --out')
    r=qualify(a.out,a.reuse_from) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)
