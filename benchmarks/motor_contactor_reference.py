"""Predeclared timer-action matrix and independent SI motor-group trajectories.

Expected actions are authored in reference-matrix.json before the simulation;
we never copy production event times into the physical reference commands.
Plateaus have large threshold margins. A separate test checks a continuous
pickup crossing against the SI circuit; this is not a full external coil model.
"""
from pathlib import Path
import argparse
import json
import numpy as np
from benchmarks.induction_reference import rotor_flux_group_reference
from power_grid_env.induction import InductionEvent
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint

KEYS=['speed_pu','e_real_pu','e_imag_pu','current_pu','bus_voltage_pu','p_w','q_var']
LIMITS=dict(zip(KEYS,[2e-5,2e-5,2e-5,1e-4,1e-5,100.,100.]))


def compare(spec,expected,out=None):
    actual=run_study(spec,isolate=True,wall_timeout_s=60.)
    if not actual.get('completed'):raise ValueError('production control solve incomplete: '+str(actual.get('status')))
    actions=[e for e in actual['event_log'] if e.get('cause') in {'undervoltage_timer','automatic_restart'}]
    identity=lambda e:(e['kind'],e['equipment_id'])
    matches=list(map(identity,actions))==list(map(identity,expected))
    time_errors=[abs(a['time_s']-b['time_s']) for a,b in zip(actions,expected)] if matches else []
    action_gate=matches and all(error<=1e-8 for error in time_errors)
    oracle=spec.model_copy(deep=True)
    for motor in oracle.induction.motors:motor.contactor=None
    oracle.induction.events += [InductionEvent.model_validate(e) for e in expected]
    oracle.induction.events.sort(key=lambda e:e.time_s)
    reference=rotor_flux_group_reference(oracle,actual)
    errors={m.id:{k:float(max(abs(np.array(actual['channels'][m.id+'.'+k])-reference[m.id][:,j]))) for j,k in enumerate(KEYS)} for m in spec.induction.motors}
    passed=action_gate and all(error<=LIMITS[key] for row in errors.values() for key,error in row.items())
    result=dict(passed=passed,code_sha256=package_fingerprint(),expected_actions=expected,actual_actions=actions,action_identity_matches=matches,action_time_errors_s=time_errors,action_time_limit_s=1e-8,maximum_errors=errors,limits=LIMITS,field_validated=False,industrial_profile_qualified=False,reference='Predeclared synthetic timer actions + separate SI physical rotor-flux circuit/DOP853; no external coil or measured qualification')
    if out is not None:
        out=Path(out);write_json(out/'production.json',actual);write_json(out/'oracle-study.json',oracle.model_dump(mode='json'));write_json(out/'si-reference.json',dict(time_s=actual['time_s'],sample_side=actual['sample_side'],channels={m:dict(zip(KEYS,v.T.tolist())) for m,v in reference.items()}));write_json(out/'comparison.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);args=p.parse_args();path=Path('cases/contactor/reference-matrix.json');matrix=json.loads(path.read_text());rows=[]
    for item in matrix['cases']:
        s=StudySpec.model_validate_json(Path(item['case']).read_text());r=compare(s,item['expected_automatic_actions'],args.out/s.id);r.update(case=item['case'],case_sha256=sha256_file(Path(item['case'])));rows.append(r)
    result=dict(passed=all(r['passed'] for r in rows),cases=rows,code_sha256=package_fingerprint(),matrix_sha256=sha256_file(path),source_sha256=sha256_file(Path(__file__)),si_source_sha256=sha256_file(Path('benchmarks/induction_reference.py')),field_validated=False,industrial_profile_qualified=False)
    write_json(args.out/'summary.json',result);print([(r['case'],r['passed'],r['action_time_errors_s']) for r in rows])
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
