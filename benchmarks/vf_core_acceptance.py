"""Registered averaged-drive physics qualification, including both power signs."""
import argparse,copy,hashlib,json
from pathlib import Path
import numpy as np
from benchmarks.vf_core_cases import NAMES,document
from benchmarks.vf_core_reference import run_reference
from power_grid_env.phase_research import run_phase_study,PhaseStudy
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint
ROOT=Path(__file__).resolve().parents[1]
FLAGS=('connected','current_limited','modulation_limited','undervoltage_derated')


def sources():
    paths=[Path(__file__),ROOT/'benchmarks/vf_core_cases.py',ROOT/'benchmarks/vf_core_reference.py',ROOT/'cases/studies/flux-saturated_dol.json']
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
    c=r['channels'];p=d['case']['drives'][0];t=np.array(r['time_s']);negative=np.maximum(0.,-np.array(c['drive.motor_power_w']));sampled_motor_recovery=float(sum((negative[1:]+negative[:-1])*.5*np.diff(t)))
    loss_ok=all(min(c['drive.'+key])>=-1e-7 for key in ['rectifier_loss_w','inverter_loss_w','motor_core_loss_w','motor_stator_copper_w','motor_rotor_copper_w','braking_power_w']);current_ok=max(c['drive.current_vector_peak_a'])<=2**.5*p['grid_current_limit_rms_a']+1e-6;energy_positive=min(c['drive.dc_energy_j'])>=0
    observed=dict(maximum_braking_duty=max(c['drive.braking_duty']),peak_braking_power_w=max(c['drive.braking_power_w']),current_limited=any(c['drive.current_limited']),modulation_limited=any(c['drive.modulation_limited']),undervoltage_derated=any(c['drive.undervoltage_derated']),minimum_motor_power_w=min(c['drive.motor_power_w']),minimum_grid_power_w=min(c['drive.grid_power_w']),sampled_motor_recovery_j=sampled_motor_recovery,final_speed_rad_s=c['drive.motor_speed_rad_s'][-1],minimum_dc_voltage_v=min(c['drive.dc_voltage_v']),nonnegative_losses=loss_ok,current_bound_respected=current_ok,nonnegative_dc_energy=energy_positive)
    required=loss_ok and current_ok and energy_positive
    if name=='core_runup':required=required and observed['final_speed_rad_s']>10.
    if name=='thermal_multicage':required=required and observed['current_limited'] and observed['modulation_limited'] and c['drive.motor_stator_temperature_c'][-1]>20.
    if name=='regeneration':required=required and observed['minimum_motor_power_w']<-10 and observed['minimum_grid_power_w']<-10 and sampled_motor_recovery>10 and observed['final_speed_rad_s']<p['motor']['initial_speed_rad_s']
    if name=='dc_undervoltage':required=required and observed['undervoltage_derated'] and observed['modulation_limited'] and max(abs(v) for v in c['drive.grid_power_w'])==0
    if name=='braking_resistor':required=required and max(c['drive.braking_duty'])>0 and min(c['drive.motor_power_w'])<-10 and max(c['drive.dc_voltage_v'])<p['braking_full_v']+.5 and max(abs(v) for v in c['drive.grid_power_w'])==0
    if name=='sag_block_recover':required=required and min(c['drive.connected'])==0 and c['drive.connected'][-1]==1 and c['drive.dc_voltage_v'][-1]>observed['minimum_dc_voltage_v']
    return dict(observed,passed=bool(required))


def qualify(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);producer=package_fingerprint();hashes=sources();rows=[]
    for name in NAMES:
        d=document(name);fine=run_phase_study(d,out/name/'fine');coarse_doc=copy.deepcopy(d);coarse_doc['emt'].update(rtol=1e-5,atol=1e-7,max_step_s=.0004);coarse=run_phase_study(coarse_doc,out/name/'coarse');ref=run_reference(d['case'],fine['time_s'],fine['sample_side']);(out/name/'reference.json').write_bytes(encoded(ref));f=comparison(d,fine,ref);c=comparison(d,coarse,ref);observed=features(name,d,fine)
        row=dict(case=name,fine=f,coarse=c,features=observed,resources={level:dict(physical_states=r['state_count'],rhs_evaluations=r['rhs_evaluations'],samples=len(r['time_s'])) for level,r in [('fine',fine),('coarse',coarse)]},improved=f['maximum_scaled_error']<=c['maximum_scaled_error'],passed=bool(f['passed'] and observed['passed'] and f['maximum_scaled_error']<=c['maximum_scaled_error']));rows.append(row);print(json.dumps({k:row[k] for k in ['case','improved','passed']}),flush=True)
    s=dict(schema='pge-vf-core-qualification/v1',producer_sha256=producer,source_hashes=hashes,rows=rows,passed=bool(all(r['passed'] for r in rows) and hashes==sources() and producer==package_fingerprint()),model='authored averaged bidirectional active rectifier/DC/Vf drive, constant efficiency and eddy-current core conductance',semiconductor_switching_modeled=False,manufacturer_accuracy_proven=False,superiority_proven=False)
    (out/'summary.json').write_bytes(encoded(s));seal(out);return s


def verify_qualification(out):
    out=Path(out);identity=verify(out);s=read_json(out/'summary.json');rows=[]
    if s['schema']!='pge-vf-core-qualification/v1' or s['producer_sha256']!=package_fingerprint() or s['source_hashes']!=sources():raise ValueError('current drive producers required')
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
