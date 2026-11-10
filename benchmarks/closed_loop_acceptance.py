"""Full bounded source/DC/PWM/FOC trajectory against a separate implementation."""
import argparse
import hashlib
from pathlib import Path
import numpy as np
from benchmarks.switching_drive_cases import document as base
from benchmarks.closed_loop_reference import run as reference
from power_grid_env.switching_drive import SwitchingDriveStudy,run_switching_drive
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint

NAMES=('nominal','grid_sag','gate_recovery','precharge')
LEVELS=(('coarse',2e-5),('fine',1e-5))
CHANNELS=['dc_voltage_v','speed_rad_s','motor_current_alpha_a','motor_current_beta_a','motor_stator_flux_alpha_vs','motor_stator_flux_beta_vs','rotor_flux_alpha_vs','rotor_flux_beta_vs','source_current_a','source_current_b','source_current_c','flux_estimate_alpha_vs','flux_estimate_beta_vs','rectifier_temperature_c','inverter_temperature_c','brake_temperature_c','gates_enabled']
CAPS=np.array([.02,.002,.005,.005,*([1e-5]*4),.01,.01,.01,1e-5,1e-5,.001,.001,.001,0.])


def document(name):
    if name not in NAMES:raise ValueError('unregistered closed-loop case')
    p=base('foc_start');p.update(id='independent-'+name,duration_s=.04,precharge_min_s=0.,switching_on_j=0.,switching_off_j=0.)
    p['control'].update(torque_enable_flux_fraction=.02,speed_ramp_s=.02)
    p['thermal'].update(resistance_alpha_per_k=0.,trip_c=[1e9]*3)
    if name=='grid_sag':p['grid_envelope']=[(0.,1.),(.012,1.),(.016,.5),(.024,.5),(.028,1.)]
    if name=='gate_recovery':p['gate_block_windows']=[(.024,.028)]
    if name=='precharge':p.update(initial_dc_v=300.,precharge_min_s=.002,precharge_resistance_per_phase_ohm=2.)
    return SwitchingDriveStudy.model_validate(p).model_dump(mode='json')


def compare(p,primary,ref):
    expected=(np.arange(round(p['duration_s']/p['sample_s'])+1)*p['sample_s']).tolist()
    if len(primary['time_s'])!=len(expected) or len(ref['time_s'])!=len(expected) or not np.allclose(primary['time_s'],expected,rtol=0,atol=5e-13) or not np.allclose(ref['time_s'],expected,rtol=0,atol=5e-13):raise ValueError('closed-loop sample coverage differs')
    a=np.array([primary['channels'][k] for k in CHANNELS]).T;b=np.array(ref['rows'])
    if a.shape!=b.shape or not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):raise ValueError('closed-loop channel inventory differs')
    error=np.max(abs(a-b),axis=0);events=[dict(time_s=e['time_s'],kind=e['kind']) for e in primary['event_log']]
    event_match=events==ref['event_log'];energy=primary['metrics']['final_energy_relative_error']<1e-7 and ref['maximum_energy_residual_j']<1e-4
    return dict(passed=bool(np.all(error<=CAPS) and event_match and energy),errors=dict(zip(CHANNELS,error.tolist())),events_match=event_match,reference_energy_residual_j=ref['maximum_energy_residual_j'],reference_equation_residual=ref['maximum_equation_residual'])


def producer():
    files=[Path(__file__),Path(__file__).with_name('closed_loop_reference.py'),Path(__file__).with_name('switching_stage_reference.py'),Path(__file__).with_name('switching_drive_cases.py')]
    return dict(package=package_fingerprint(),benchmarks={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def run(out,replay=False):
    out=Path(out);before=producer()
    if replay:
        manifest=verify(out)
        if read_json(out/'producer.json')!=before:raise ValueError('producer differs')
    else:out.mkdir(parents=True,exist_ok=False);(out/'producer.json').write_bytes(encoded(before))
    def store(name,value):
        if replay:
            if encoded(read_json(out/name))!=encoded(value):raise ValueError('closed-loop replay differs: '+name)
        else:(out/name).write_bytes(encoded(value))
    rows=[]
    for name in NAMES:
        p=document(name);store(name+'-study.json',p);runs={};refs={};checks={}
        for level,step in LEVELS:
            study=dict(p,max_step_s=step);runs[level]=run_switching_drive(study);refs[level]=reference(study)
            store(name+'-'+level+'-primary.json',runs[level]);store(name+'-'+level+'-reference.json',refs[level]);checks[level]=compare(study,runs[level],refs[level])
        difference={provider:dict(zip(CHANNELS[:-1],np.max(abs(np.array(fine)-np.array(coarse)),axis=0).tolist())) for provider,coarse,fine in [('primary',np.array([runs['coarse']['channels'][k] for k in CHANNELS[:-1]]).T,np.array([runs['fine']['channels'][k] for k in CHANNELS[:-1]]).T),('reference',np.array(refs['coarse']['rows'])[:,:-1],np.array(refs['fine']['rows'])[:,:-1])]}
        c=runs['fine']['channels'];times=runs['fine']['time_s']
        features=c['speed_rad_s'][-1]>.01 and max(c['torque_enabled'])>0
        if name=='gate_recovery':
            blocked=[i for i,t in enumerate(times) if .0241<t<.0279];recovered=[i for i,t in enumerate(times) if .032<t<.038]
            features=features and bool(blocked and recovered) and not any(c['gates_enabled'][i] for i in blocked) and all(c['gates_enabled'][i] for i in recovered)
        if name=='precharge':features=features and any(e['kind']=='precharge_bypass' and e['time_s']>=.002 for e in runs['fine']['event_log'])
        row=dict(case=name,passed=bool(features and all(c['passed'] for c in checks.values())),physical_features_passed=bool(features),checks=checks,step_differences=difference,final_speed_rad_s=c['speed_rad_s'][-1]);rows.append(row);print(encoded(row).decode(),flush=True)
    result=dict(schema='power-grid-env.closed-loop-independent/v1',passed=all(r['passed'] for r in rows),rows=rows,primary_trajectories=8,independent_trajectories=8,scope='full bounded RL/source/diode/DC/PWM/nominal FOC/machine/shaft/electronics RC; same midpoint/discrete-gradient mathematical scheme, separate mixed solver/control/PWM; no saturation/multicage/Gc/winding thermal/switching-energy/thermal trips; no external simulator/full industrial qualification',superiority_established=False)
    store('summary.json',result)
    if producer()!=before:raise ValueError('producer changed during run')
    identity=verify(out) if replay else seal(out)
    if replay and identity!=manifest:raise ValueError('artifact changed during replay')
    print(encoded(dict(passed=result['passed'],replayed=replay,manifest_sha256=identity)).decode(),flush=True)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();g=parser.add_mutually_exclusive_group(required=True);g.add_argument('--out');g.add_argument('--verify');a=parser.parse_args();run(a.verify or a.out,bool(a.verify))
