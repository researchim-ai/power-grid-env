"""Replayable bounded saturated multicage observer acceptance, no external CI."""
import argparse
import hashlib
from pathlib import Path
import numpy as np
from benchmarks.multicage_observer_cases import document,NAMES
from benchmarks.multicage_observer_reference import reference
from power_grid_env.switching_drive import SwitchingDriveStudy,SwitchingDrive,run_switching_drive
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint

LEVELS=(('coarse',2e-5),('fine',1e-5),('finest',5e-6))


def producer():
    root=Path(__file__).resolve().parent
    return dict(package=package_fingerprint(),benchmarks={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [root/'multicage_observer_acceptance.py',root/'multicage_observer_reference.py',root/'multicage_observer_cases.py',root/'switching_extended_cases.py',root/'switching_drive_cases.py']})


def continuous(name):
    d=document(name);p=SwitchingDriveStudy.model_validate(d)
    current=8-3j;speed=-25.;duration=.006
    expected=reference(p.observer_motor,current,speed,duration)
    runs={};errors=[]
    for count in [30,60,120]:
        observer=SwitchingDrive(p).controller
        for _ in range(count):observer.observe(current,speed,duration/count)
        error=float(max(abs(observer.rotor_flux.ravel()-expected)))
        runs[str(count)]=dict(rotor_flux=observer.rotor_flux.tolist(),error_vs=error);errors.append(error)
    return dict(reference=expected.tolist(),runs=runs,passed=bool(errors[-1]<2e-7 and errors[0]>3.8*errors[1] and errors[1]>3.8*errors[2]))


def assessment(results):
    finest=results['finest'];c=finest['channels']
    errors={}
    for level in ['coarse','fine']:
        values=[]
        for channel,scale in [('dc_voltage_v',1.),('speed_rad_s',1.),('flux_estimate_vs',.01)]:
            values.append(float(max(abs(np.array(results[level]['channels'][channel])-c[channel])))/scale)
        errors[level]=max(values)
    # Carrier rate remains fixed when refining electrical substeps. This gate
    # measures electrical convergence, not observer sampling convergence.
    events=[[ (e['kind'],e.get('component')) for e in r['event_log']] for r in results.values()]
    passed=all(r['completed'] and r['metrics']['final_energy_relative_error']<1e-7 and r['metrics']['kcl_max_a']<5e-5 and r['metrics']['motor_constitutive_residual_v']<1e-8 for r in results.values())
    passed=passed and max(c['torque_enabled'])>0 and c['speed_rad_s'][-1]>.01
    passed=passed and errors['fine']<errors['coarse'] and errors['fine']<.5 and all(e==events[0] for e in events)
    return dict(passed=bool(passed),scaled_errors=errors,event_sequences_equal=all(e==events[0] for e in events),final_speed_rad_s=c['speed_rad_s'][-1],final_flux_estimate_vs=c['flux_estimate_vs'][-1])


def run(out,replay=False):
    out=Path(out);before=producer()
    if replay:
        manifest=verify(out)
        if read_json(out/'producer.json')!=before:raise ValueError('producer differs; rerun into a new directory')
    else:
        out.mkdir(parents=True,exist_ok=False);(out/'producer.json').write_bytes(encoded(before))
    def retain(path,value):
        if replay:
            if encoded(read_json(out/path))!=encoded(value):raise ValueError('replayed content differs: '+path)
        else:(out/path).write_bytes(encoded(value))
    rows=[]
    for name in NAMES:
        d=document(name);retain(name+'-study.json',d);results={}
        for level,step in LEVELS:
            study=dict(d,max_step_s=step)
            results[level]=run_switching_drive(study)
            retain(name+'-'+level+'.json',results[level])
        oracle=continuous(name);retain(name+'-continuous.json',oracle)
        row=dict(case=name,**assessment(results),continuous_passed=oracle['passed']);rows.append(row)
        print(encoded(row).decode(),flush=True)
    summary=dict(schema='power-grid-env.multicage-observer-acceptance/v1',passed=all(r['passed'] and r['continuous_passed'] for r in rows),rows=rows,native_drive_trajectories=12,independent_continuous_cases=4,independent_full_drive_trajectory=False,scope='authored 80ms balanced-source PWM drive; fixed carrier; measured current/speed; zero-Gc nominal observer; no temperature adaptation, OEM fit or independent full controller')
    retain('summary.json',summary)
    if producer()!=before:raise ValueError('producer changed while running')
    identity=verify(out) if replay else seal(out)
    if replay and identity!=manifest:raise ValueError('artifact changed during replay')
    print(encoded(dict(passed=summary['passed'],replayed=replay,manifest_sha256=identity)).decode(),flush=True)
    if not summary['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--out');group.add_argument('--verify');args=parser.parse_args();run(args.verify or args.out,bool(args.verify))
