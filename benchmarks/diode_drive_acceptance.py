"""Registered passive diode-fed drive qualification, with independent circuit replay."""
import argparse,copy,hashlib,json
from pathlib import Path
import numpy as np
from benchmarks.diode_drive_cases import NAMES,document
from benchmarks.vf_core_reference import run_reference
from power_grid_env.phase_research import run_phase_study,PhaseStudy
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint
ROOT=Path(__file__).resolve().parents[1]
FLAGS=('current_control_limited','precharge_bypassed','rectifier_conducting','connected','current_limited','modulation_limited','undervoltage_derated')


def sources():
    paths=[Path(__file__),ROOT/'benchmarks/vf_core_cases.py',ROOT/'benchmarks/diode_drive_cases.py',ROOT/'benchmarks/vf_core_reference.py',ROOT/'cases/studies/flux-saturated_dol.json']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def tolerance(key):
    if key.endswith(FLAGS):return 0.
    if key.endswith(('_temperature_c',)):return 2e-6
    if key.endswith('_duty'):return 1e-6
    if key.endswith(('_flux_alpha_vs','_flux_beta_vs')):return 1e-6
    if key.endswith(('_w',)):return .05
    if key.endswith(('_j',)):return .001
    if key.endswith(('_voltage_v','.voltage_v')):return .002
    if key.endswith(('_frequency_hz',)):return 1e-4
    return 5e-4


def comparison(d,result,reference):
    if result['time_s']!=reference['time_s'] or result['sample_side']!=reference['sample_side'] or len(reference['records'])!=len(result['time_s']) or reference['primary_rhs_used']:raise ValueError('drive reference sample inventory differs')
    errors={};ratios=[]
    expected={key for key in result['channels'] if key.startswith('drive.') or key.startswith(('a.','b.','c.','line'))}
    if any(set(r['channels'])!=expected for r in reference['records']):raise ValueError('reference channel inventory differs')
    for key in sorted(expected):
        actual=np.array(result['channels'][key]);ref=np.array([r['channels'][key] for r in reference['records']]);error=float(max(abs(actual-ref)));limit=tolerance(key)
        errors[key]=error
        ratios.append(0. if not error else 1.+error if not limit else error/(limit+1e-5*max(abs(ref))))
    e=result['energy'][-1];normalizer=max(1.,abs(e['entered_j']),abs(e['loss_j']));primary=abs(e['residual_j'])/normalizer;independent=max(abs(r['energy_error_j']) for r in reference['records'])/normalizer
    return dict(errors=errors,maximum_scaled_error=float(max(ratios)),primary_energy_relative_error=primary,reference_energy_relative_error=independent,kcl_max_a=result['kcl_max_a'],passed=bool(max(ratios)<=1 and primary<=1e-6 and independent<=1e-7 and result['kcl_max_a']<=1e-7))


def features(name,d,r):
    c=r['channels'];p=d['case']['drives'][0]
    minimum_grid=min(c['drive.grid_power_w']);minimum_current=min(c['drive.rectifier_current_a'])
    passive=minimum_grid>=-1e-7 and minimum_current>=0 and min(c['drive.rectifier_loss_w'])>=-1e-7
    observed=dict(minimum_grid_power_w=minimum_grid,minimum_rectifier_current_a=minimum_current,
        minimum_motor_power_w=min(c['drive.motor_power_w']),peak_dc_voltage_v=max(c['drive.dc_voltage_v']),
        final_dc_voltage_v=c['drive.dc_voltage_v'][-1],peak_rectifier_current_a=max(c['drive.rectifier_current_a']),
        peak_braking_power_w=max(c['drive.braking_power_w']),passive=passive)
    required=passive and min(c['drive.dc_voltage_v'])>=0
    if name in {'precharge','early_bypass','sag_reconnect'}:
        required=required and c['drive.dc_voltage_v'][0]==0 and observed['final_dc_voltage_v']>100 and any(c['drive.precharge_bypassed']) and max(c['drive.precharge_loss_w'])>0
    if name=='sag_reconnect':required=required and min(c['drive.connected'])==0 and c['drive.connected'][-1]==1
    if name.endswith('regeneration'):
        # A passive bridge may still import power during initial RLC ringing.
        # Reverse blocking is not a promise of zero forward current throughout.
        blocked=[i for i,pm in enumerate(c['drive.motor_power_w']) if pm<-10 and c['drive.rectifier_current_a'][i]==0]
        observed['regenerative_blocked_samples']=len(blocked)
        required=required and observed['minimum_motor_power_w']<-10 and observed['peak_dc_voltage_v']>p['initial_dc_v'] and bool(blocked)
    if name.startswith('current_control'):required=required and 'drive.current_tracking_error_a' in c and any(c['drive.current_control_limited']) and max(c['drive.motor_current_peak_a'])>1.
    if name=='braked_regeneration':required=required and observed['peak_braking_power_w']>100
    if name=='blocked_regeneration':required=required and observed['peak_braking_power_w']==0
    return dict(observed,passed=bool(required))


def qualify(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);producer=package_fingerprint();hashes=sources();rows=[]
    for name in NAMES:
        d=document(name);fine=run_phase_study(d,out/name/'fine');coarse_doc=copy.deepcopy(d);coarse_doc['emt'].update(rtol=1e-5,atol=1e-7,max_step_s=.0004);coarse=run_phase_study(coarse_doc,out/name/'coarse');ref=run_reference(d['case'],fine['time_s'],fine['sample_side']);(out/name/'reference.json').write_bytes(encoded(ref));f=comparison(d,fine,ref);c=comparison(d,coarse,ref);observed=features(name,d,fine)
        row=dict(case=name,fine=f,coarse=c,features=observed,resources={level:dict(physical_states=r['state_count'],rhs_evaluations=r['rhs_evaluations'],samples=len(r['time_s'])) for level,r in [('fine',fine),('coarse',coarse)]},improved=f['maximum_scaled_error']<=c['maximum_scaled_error'],passed=bool(f['passed'] and observed['passed'] and f['maximum_scaled_error']<=c['maximum_scaled_error']));rows.append(row);print(json.dumps({k:row[k] for k in ['case','improved','passed']}),flush=True)
    s=dict(schema='pge-diode-drive-qualification/v1',producer_sha256=producer,source_hashes=hashes,rows=rows,passed=bool(all(r['passed'] for r in rows) and hashes==sources() and producer==package_fingerprint()),model='six resistive ideal-blocking diodes, finite DC path R, precharge bypass and averaged V/f inverter; explicit RLC/core-loss machine',semiconductor_switching_modeled=False,manufacturer_accuracy_proven=False,superiority_proven=False)
    (out/'summary.json').write_bytes(encoded(s));seal(out);return s


def verify_qualification(out):
    out=Path(out);identity=verify(out);s=read_json(out/'summary.json');rows=[]
    if s['schema']!='pge-diode-drive-qualification/v1' or s['producer_sha256']!=package_fingerprint() or s['source_hashes']!=sources():raise ValueError('current drive producers required')
    for name in NAMES:
        d=document(name);ref=read_json(out/name/'reference.json');results=[]
        for level in ['fine','coarse']:
            folder=out/name/level;verify(folder);expected=copy.deepcopy(d)
            if level=='coarse':expected['emt'].update(rtol=1e-5,atol=1e-7,max_step_s=.0004)
            if read_json(folder/'study.json')!=PhaseStudy.model_validate(expected).model_dump(mode='json'):raise ValueError('registered drive study differs')
            r=read_json(folder/'result.json')
            if r['code_sha256']!=package_fingerprint() or max(r['time_s'])!=d['emt']['duration_s'] or len(r['time_s'])>5000:raise ValueError('drive producer/time budget differs')
            results.append(r)
        if ref['time_s']!=results[0]['time_s'] or ref['sample_side']!=results[0]['sample_side']:raise ValueError('reference time inventory differs')
        replay=run_reference(d['case'],ref['time_s'],ref['sample_side'])
        if len(replay['records'])!=len(ref['records']):raise ValueError('reference replay inventory differs')
        for a,b in zip(replay['records'],ref['records'],strict=True):
            if a['channels'].keys()!=b['channels'].keys() or any(abs(a['channels'][k]-b['channels'][k])>1e-8*(1+abs(a['channels'][k])) for k in a['channels']) or abs(a['energy_error_j']-b['energy_error_j'])>1e-6:raise ValueError('independent drive replay differs')
        fine,coarse=results;f=comparison(d,fine,ref);c=comparison(d,coarse,ref);observed=features(name,d,fine);rows.append(dict(case=name,fine=f,coarse=c,features=observed,resources={level:dict(physical_states=r['state_count'],rhs_evaluations=r['rhs_evaluations'],samples=len(r['time_s'])) for level,r in [('fine',fine),('coarse',coarse)]},improved=f['maximum_scaled_error']<=c['maximum_scaled_error'],passed=bool(f['passed'] and observed['passed'] and f['maximum_scaled_error']<=c['maximum_scaled_error'])))
    if s['rows']!=rows or s['passed']!=all(r['passed'] for r in rows) or any(s[k] for k in ['semiconductor_switching_modeled','manufacturer_accuracy_proven','superiority_proven']):raise ValueError('drive qualification conclusion differs')
    return dict(verified=True,passed=s['passed'],manifest_sha256=identity,independent_reference_replayed=True,scope='stored native trajectories, independently replayed RLC/flux/controller equations; no native replay, hardware qualification or authenticated host')


if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=p.parse_args();r=qualify(a.out) if a.out else verify_qualification(a.verify);print(json.dumps(r));raise SystemExit(0 if r['passed'] else 2)
