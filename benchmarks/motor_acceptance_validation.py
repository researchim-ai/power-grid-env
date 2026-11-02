"""Explicit acceptance policy on DOL/restart and deliberately unsuccessful cases."""
from pathlib import Path
import argparse
from benchmarks.multibus_motor_cases import example as motors
from benchmarks.multibus_restart_cases import example as restart
from benchmarks.native_online_validation import source_identity
from power_grid_env.motor_assessment import MotorAcceptanceCriteria,run
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.report import write_json,sha256_file

NAMES=('normal_runup','short_runup','grid_restart','thermal_inhibit','manual_inhibit','restart_limit')
EXPECTED=(True,False,True,False,False,False)


def example(name):
    if name not in NAMES:raise ValueError('unknown acceptance case')
    s=motors('runup3s') if name in ('normal_runup','short_runup') else restart(name)
    if name=='short_runup':s.induction.duration_s=.6
    from power_grid_env.study_spec import StudySpec
    return StudySpec.model_validate(s.model_dump(mode='json'))


def validate(out):
    if out.exists() and any(out.iterdir()):raise ValueError('new acceptance corpus directory required')
    p=dict(code_sha256=package_fingerprint(),source_sha256=source_identity(),criteria=MotorAcceptanceCriteria().model_dump(mode='json'),cases={n:dict(study_sha256=canonical_hash(example(n)),expected_acceptance=e) for n,e in zip(NAMES,EXPECTED)})
    write_json(out/'protocol.json',p);write_json(out/'summary.json',dict(passed=False,status='running'));rows=[]
    for name,wanted in zip(NAMES,EXPECTED):
        print(name,flush=True);result=run(example(name),MotorAcceptanceCriteria(),out/name)
        if not result['calculation_completed'] or result['passed']!=wanted:raise ValueError('unexpected motor acceptance '+name)
        rows.append(dict(case=name,accepted=result['passed'],expected_acceptance=wanted))
    if package_fingerprint()!=p['code_sha256'] or source_identity()!=p['source_sha256']:raise ValueError('assessment producer changed')
    r=dict(passed=True,cases=rows,accepted_startups=sum(r['accepted'] for r in rows),criteria_failures=sum(not r['accepted'] for r in rows),all_calculations_completed=True,code_sha256=p['code_sha256'],artifacts_sha256={str(f.relative_to(out)):sha256_file(f) for f in out.rglob('*.json') if f!=out/'summary.json'})
    write_json(out/'summary.json',r);return r


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);a=p.parse_args();print(validate(a.out))
