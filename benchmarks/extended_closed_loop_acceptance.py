"""Registered independent nonlinear/electrothermal full-drive qualification.

Not an external simulator benchmark. Limits are fixed before the runs; elapsed
runtime is deliberately not a pass criterion. No physical relay or switch-loss
jumps are admitted by this profile.
"""
import argparse
import copy
import hashlib
from pathlib import Path
import numpy as np
from benchmarks.numerical_replay import assert_replay_equal
from benchmarks.switching_extended_cases import document as electrical
from benchmarks.switching_thermal_cases import document as electrothermal
from benchmarks.extended_closed_loop_reference import run as reference,channels
from power_grid_env.switching_drive import SwitchingDriveStudy,run_switching_drive
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint

NAMES=('saturated_single_core','double_cage','saturated_four_core','warm_four_winding')
LEVELS=(('coarse',2e-5),('fine',1e-5))


def document(name):
    if name not in NAMES:raise ValueError('unregistered extended closed-loop case')
    p=copy.deepcopy(electrothermal('saturated_four_thermal') if name=='warm_four_winding' else electrical({'saturated_single_core':'saturated_core','double_cage':'double_cage','saturated_four_core':'saturated_four_cage'}[name]))
    p.update(id='independent-extended-'+name,duration_s=.04,precharge_min_s=0.,switching_on_j=0.,switching_off_j=0.)
    p['control'].update(torque_enable_flux_fraction=.02,speed_ramp_s=.02)
    p['thermal'].update(resistance_alpha_per_k=0.,trip_c=[1e9]*3)
    if name=='saturated_four_core':p['grid_envelope']=[(0.,1.),(.012,1.),(.016,.5),(.024,.5),(.028,1.)];p['gate_block_windows']=[(.024,.028)]
    if name=='warm_four_winding':p.update(motor_initial_temperature_c=[90.,70.],motor_temperature_trip_c=[140.,140.])
    return SwitchingDriveStudy.model_validate(p).model_dump(mode='json')


def caps(p):
    result={k:1e-5 for k in channels(p)}
    result.update(dc_voltage_v=.02,speed_rad_s=.002,motor_current_alpha_a=.005,motor_current_beta_a=.005,source_current_a=.01,source_current_b=.01,source_current_c=.01,gates_enabled=0.)
    for k in result:
        if 'temperature_c' in k:result[k]=.001
    return result


def compare(p,primary,ref):
    keys=channels(p);expected=np.arange(round(p['duration_s']/p['sample_s'])+1)*p['sample_s']
    for r in [primary,ref]:
        if len(r['time_s'])!=len(expected) or not np.allclose(r['time_s'],expected,rtol=0,atol=5e-13):raise ValueError('extended sample coverage differs')
    if ref['row_channels']!=keys:raise ValueError('extended reference channel inventory differs')
    a=np.array([primary['channels'][k] for k in keys]).T;b=np.asarray(ref['rows'])
    if a.shape!=b.shape or not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):raise ValueError('extended channel inventory or finiteness differs')
    errors=dict(zip(keys,np.max(abs(a-b),axis=0).tolist()));limits=caps(p)
    events=[dict(time_s=e['time_s'],kind=e['kind']) for e in primary['event_log']]
    events_match=events==ref['event_log']
    audits=dict(primary_energy=primary['metrics']['final_energy_relative_error']<1e-7,reference_energy=ref['maximum_energy_residual_j']<1e-4,primary_kcl=primary['metrics']['kcl_max_a']<5e-5,primary_magnetic=primary['metrics']['motor_constitutive_residual_v']<1e-8,reference_equations=ref['maximum_equation_residual']<=2e-6)
    heat_errors={k:abs(primary['metrics'][k]-ref[k]) for k in ['motor_core_heat_j','motor_stator_heat_j','motor_rotor_heat_j']}
    return dict(passed=bool(all(errors[k]<=limits[k] for k in keys) and events_match and all(audits.values()) and max(heat_errors.values())<1e-5),errors=errors,limits=limits,heat_errors_j=heat_errors,heat_limit_j=1e-5,events_match=events_match,audits=audits,reference_energy_residual_j=ref['maximum_energy_residual_j'],reference_equation_residual=ref['maximum_equation_residual'])


def features(name,p,result):
    c=result['channels'];t=np.array(result['time_s']);f=dict(nonzero_torque=bool(max(c['torque_enabled'])>0),active_pwm=result['metrics']['gate_edges']>10,nonzero_rotor_heat=result['metrics']['motor_rotor_heat_j']>0.)
    if p['motor'].get('core_loss_s',0.):f['positive_core_heat']=result['metrics']['motor_core_heat_j']>0.
    if p['motor']['saturation_beta_per_vs']:f['nontrivial_saturation']=(p['motor']['saturation_beta_per_vs']*max(np.hypot(c['motor_stator_flux_alpha_vs'],c['motor_stator_flux_beta_vs'])))**p['motor']['saturation_exponent']>.1
    if name=='saturated_four_core':
        blocked=(t>.0241)&(t<.0279);recovered=(t>.032)&(t<.038)
        f['gate_block_and_recovery']=bool(any(blocked) and any(recovered) and not np.any(np.array(c['gates_enabled'])[blocked]) and np.all(np.array(c['gates_enabled'])[recovered]))
    if name=='warm_four_winding':f['temperature_coupling']=abs(c['motor_stator_temperature_c'][-1]-90.)>1e-4 and abs(c['motor_rotor_temperature_c'][-1]-70.)>1e-4
    return {k:bool(v) for k,v in f.items()}


def producer():
    names=['extended_closed_loop_acceptance.py','extended_closed_loop_reference.py','closed_loop_reference.py','switching_stage_reference.py','switching_extended_cases.py','switching_thermal_cases.py','switching_drive_cases.py','numerical_replay.py']
    return dict(package=package_fingerprint(),benchmarks={n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in names})


def run(out,replay=False):
    out=Path(out);before=producer()
    if replay:
        manifest=verify(out)
        if read_json(out/'producer.json')!=before:raise ValueError('extended producer differs')
    else:out.mkdir(parents=True,exist_ok=False);(out/'producer.json').write_bytes(encoded(before))
    def store(name,value):
        if replay:
            assert_replay_equal(read_json(out/name),value)
        else:(out/name).write_bytes(encoded(value))
    rows=[]
    for name in NAMES:
        p=document(name);store(name+'-study.json',p);checks={};runs={};refs={}
        for level,step in LEVELS:
            study=dict(p,max_step_s=step);a=run_switching_drive(study);b=reference(study);runs[level]=a;refs[level]=b
            store(name+'-'+level+'-primary.json',a);store(name+'-'+level+'-reference.json',b);checks[level]=compare(study,a,b)
        keys=channels(p);differences={}
        for label,values in [('primary',runs),('reference',refs)]:
            arrays=[np.array([values[level]['channels'][k] for k in keys]).T if label=='primary' else np.array(values[level]['rows']) for level,_ in LEVELS]
            differences[label]=dict(zip(keys,np.max(abs(arrays[0]-arrays[1]),axis=0).tolist()))
        flags=features(name,p,runs['fine']);row=dict(case=name,passed=all(x['passed'] for x in checks.values()) and all(flags.values()),checks=checks,physical_features=flags,step_differences=differences)
        rows.append(row);print(encoded(row).decode(),flush=True)
    result=dict(schema='power-grid-env.extended-closed-loop-independent/v1',passed=all(r['passed'] for r in rows),rows=rows,primary_trajectories=2*len(NAMES),independent_trajectories=2*len(NAMES),scope='fixed source/RL/diode/DC/nominal FOC/PWM; 1/2/4 cage, radial saturation, Gc, winding RC/R(T), sag/gate recovery; independent mixed equations, same midpoint/discrete-gradient scheme; authored parameters; no switching energy/thermal trips/variable device resistance/industrial qualification/external simulator ranking',superiority_established=False)
    store('summary.json',result)
    if producer()!=before:raise ValueError('extended producer changed during run')
    identity=verify(out) if replay else seal(out)
    if replay and identity!=manifest:raise ValueError('extended artifact changed during replay')
    print(encoded(dict(passed=result['passed'],replayed=replay,manifest_sha256=identity)).decode(),flush=True)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();g=parser.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=parser.parse_args();run(a.verify or a.out,bool(a.verify))
