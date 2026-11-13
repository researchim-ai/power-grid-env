"""Independent mixed-equation checks on accepted motor-zero event pieces.

Free motor common mode is a gauge; compare alpha/beta voltage, not that gauge.
The independent reference uses the same midpoint/discrete-gradient scheme.
"""
import argparse
import hashlib
from pathlib import Path
import numpy as np
from benchmarks.switching_stage_reference import solve_stage
from power_grid_env.switching_drive import SwitchingDrive,SwitchingDriveStudy
from power_grid_env.switching_adaptive import clone_trial
from power_grid_env.pwm_bridge import AB
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded

CAPS=dict(flux_vs=1e-10,network_voltage_v=1e-3,motor_ab_voltage_v=1e-3,line_current_a=1e-7,speed_rad_s=1e-8,reference_residual=2e-6)


def run():
    before=package_fingerprint();rows=[]
    for name in ['switching_motor_zero','switching_motor_coincident']:
        path=Path('tests/fixtures')/(name+'.json');d=read_json(path);m=SwitchingDrive(SwitchingDriveStudy.model_validate(d['study']))
        for k,v in d['state'].items():
            old=getattr(m,k);setattr(m,k,np.asarray(v,dtype=old.dtype) if isinstance(old,np.ndarray) else v)
        m.initial_energy=m.stored();m._adaptive_next_h=d.get('proposal_s',d['step_s']);native=clone_trial(m);g=np.asarray(d['gates'],bool)
        h,_=m.step(d['time_s'],d['step_s'],g);pieces=m.steps;checks=[]
        for index in range(pieces):
            dt=h/pieces;t=d['time_s']+index*dt
            state={k:getattr(native,k).tolist() for k in ['flux','line','voltage','temperature','winding_temperature']};state['speed']=native.speed
            ref=solve_stage(native.p.model_dump(mode='json'),state,t,dt,g,native.bypassed)
            actual,q=native._midpoint_step(t,dt,g)
            def error(a,b):return float(np.max(abs(np.asarray(a)-np.asarray(b))))
            errors=dict(flux_vs=error(q['flux_mid'],ref['flux_mid']),network_voltage_v=error(q['voltage'][:5],ref['voltage'][:5]),motor_ab_voltage_v=error(AB@q['voltage'][5:],AB@ref['voltage'][5:]),line_current_a=error(q['line_mid'],ref['line_mid']),speed_rad_s=abs(q['speed_mid']-ref['speed_mid']),reference_residual=ref['residual_max'])
            checks.append(dict(index=index,step_s=dt,errors=errors,passed=actual==dt and all(np.isfinite(v) and v<=CAPS[k] for k,v in errors.items())))
        rows.append(dict(case=name,passed=all(c['passed'] for c in checks),fixture_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),pieces=checks,motor_events=m.adaptive_mesh_events))
    if package_fingerprint()!=before:raise RuntimeError('stage-check producer changed')
    return dict(schema='power-grid-env.motor-zero-independent-stages/v1',passed=all(r['passed'] for r in rows),code_sha256=before,reference_source_sha256=hashlib.sha256(Path('benchmarks/switching_stage_reference.py').read_bytes()).hexdigest(),limits=CAPS,rows=rows,absolute_error_certified=False,scope='independent mixed equations at accepted native event pieces; same midpoint/discrete-gradient scheme; motor common-mode gauge excluded; not a full independent trajectory')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);a=p.parse_args();r=run();Path(a.out).write_bytes(encoded(r));print(r['passed']);raise SystemExit(0 if r['passed'] else 2)
