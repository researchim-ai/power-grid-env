"""Independent SI continuous-voltage/timer/thermal closed-loop motor validation."""
from pathlib import Path
import argparse
import numpy as np
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json,sha256_file
from benchmarks.genrou_coupled_reference import reference
from benchmarks.independent_contactor import run as independent_hybrid

NAMES=('basic','short_sag','hysteresis','minimum_off','manual_inhibit','deadline_recovery','retry_limit','thermal_trip','thermal_before_dropout','continuous_voltage','initially_open','manual_close_loss','pickup_deadline_loss','thermal_after_manual_close')
DISCRETE=('connected','thermal_latched','contactor_voltage_available','contactor_pending_restart','contactor_restart_count','contactor_timer_active')


def example(name):
    if name not in NAMES:raise ValueError('unknown independent automatic motor case')
    return StudySpec.model_validate_json(Path('cases/studies/automatic_coupled_'+name+'.json').read_text())


def event_signature(event):
    kind=event['kind']
    if kind.startswith('contactor_') or kind=='thermal_trip':return kind,event['equipment_id']
    if kind in {'motor_open','motor_close'}:return kind,event['equipment_id'],event.get('cause','manual'),event['outcome']
    return None


def compare(spec,out=None):
    if not spec.coupled.motors[0].contactor:raise ValueError('automatic contactor passport required')
    actual=run_study(spec,isolate=True,wall_timeout_s=60.)
    if not actual.get('completed'):raise ValueError('production automatic study incomplete: '+str(actual))
    independent=reference(spec,actual,hybrid_runner=independent_hybrid)
    errors={key:float(max(abs(np.array(values)-actual['channels'][key]))) for key,values in independent['channels'].items()}
    def limit(key):
        if key.split('.')[-1] in DISCRETE:return 0.
        if key.endswith(('.p_w','.q_var')):return 100.
        if key.endswith('contactor_timer_remaining_s'):return 1e-6
        if key.endswith(('.omega','.speed_pu','.delta','contactor_sensed_voltage_pu')):return 1e-5
        return 1e-4
    limits={k:limit(k) for k in errors}
    expected=[e for e in independent['independent_event_log'] if event_signature(e) is not None]
    observed=[e for e in actual['event_log'] if event_signature(e) is not None]
    events_match=[event_signature(e) for e in expected]==[event_signature(e) for e in observed]
    time_error=max([abs(a['time_s']-b['time_s']) for a,b in zip(expected,observed)],default=0.) if events_match else None
    passed=bool(all(np.isfinite(errors[k]) and errors[k]<=v for k,v in limits.items()) and events_match and time_error<=1e-6)
    result=dict(case_id=spec.id,passed=passed,maximum_errors=errors,limits=limits,events_match=events_match,event_time_tolerance_s=1e-6,maximum_event_time_error_s=time_error,
        expected_events=expected,observed_events=[dict(time_s=e['time_s'],kind=e['kind'],equipment_id=e['equipment_id'],**{k:e[k] for k in ['cause','outcome'] if k in e}) for e in observed],
        field_validated=False,industrial_profile_qualified=False)
    if out is not None:
        out=Path(out);write_json(out/'study.json',spec.model_dump(mode='json'));write_json(out/'production.json',actual)
        write_json(out/'independent.json',dict(time_s=actual['time_s'],sample_side=actual['sample_side'],**independent))
        result['artifacts_sha256']={p.name:sha256_file(p) for p in out.glob('*.json') if p.name!='comparison.json'}
        write_json(out/'comparison.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    write_json(a.out/'summary.json',dict(passed=False,status='running'));rows=[]
    try:
        for name in NAMES:
            row=compare(example(name),a.out/name);rows.append(row);print(name,row['passed'],row['maximum_event_time_error_s'],flush=True)
        result=dict(passed=all(r['passed'] for r in rows),cases=rows,code_sha256=package_fingerprint(),
            source_sha256={str(p):sha256_file(p) for p in map(Path,[__file__,'benchmarks/independent_contactor.py','benchmarks/genrou_coupled_reference.py','benchmarks/genrou_reference.py','benchmarks/induction_reference.py'])},
            case_files_sha256={str(p):sha256_file(p) for p in [Path('cases/studies/automatic_coupled_'+n+'.json') for n in NAMES]},
            scope='One double-cage motor and GENROU/TGOV1/SEXS on balanced common bus; independently integrated voltage roots, hysteresis/timers/restart count and optional latched stator i2 thermal replica',
            independent_control_decisions=True,production_control_decisions_as_oracle=False,native_closed_loop_controller_qualified=False,field_validated=False,industrial_profile_qualified=False)
    except Exception as exc:
        write_json(a.out/'summary.json',dict(passed=False,status='failed',error=str(exc)[:2000],cases=rows));raise
    write_json(a.out/'summary.json',result)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
