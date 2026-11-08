"""Eight engineering workflows vs independent SI flux/thermal trajectories.

Authored common-bus fixtures. Contactor clocks are derived from the prescribed
deep source plateau and passports, never from production event records. This
does not qualify arbitrary threshold crossings or continuous-time extrema.
"""
from pathlib import Path
import argparse
import copy
import numpy as np

from power_grid_env.motor_engineering import run_engineering,verify_engineering
from power_grid_env.study_artifacts import read_json,encoded,seal,digest
from power_grid_env.study_spec import StudySpec
from power_grid_env.induction import InductionEvent
from benchmarks.induction_reference import rotor_flux_group_reference
from benchmarks.thermal_motor_reference import reference as thermal_reference

ROOT=Path(__file__).resolve().parents[1]
KEYS=['speed_pu','e_real_pu','e_imag_pu','current_pu','bus_voltage_pu','p_w','q_var']
LIMITS=dict(zip(KEYS,[2e-5,2e-5,2e-5,1e-4,1e-5,100.,100.]))


def example():
    base=read_json(ROOT/'cases/industrial-dol-examples.json')
    cases=[c for c in base['cases'] if c['study']['study']=='motor_start']
    # Coarse 10 ms thermal quadrature failed the unchanged 5e-4 replica gate.
    # Use measured refinement, not a relaxed acceptance threshold.
    next(c for c in cases if c['id']=='thermal_cold_trip')['study']['induction']['step_s']=.001
    study=read_json(ROOT/'cases/studies/motor_contactor_staggered_group.json')
    automatic=dict(id='automatic_staggered_restart',study=study,
        parameter_provenance='Authored synthetic common-bus DOL contactor passports; not OEM/field data',
        motors=[dict(motor_id=m['id'],minimum_final_speed_pu=.95,maximum_sampled_current_pu=6.5,
                     recovery=dict(from_s=.5,minimum_speed_pu=.95,dwell_s=.05,maximum_delay_s=1.)) for m in study['induction']['motors']],
        voltage_windows=[dict(node_id='motor',from_s=.55,to_s=1.5,minimum_voltage_pu=.85,maximum_voltage_pu=1.05)],expectation='within_limits')
    cases.append(automatic);late=copy.deepcopy(automatic);late['id']='late_restart_unrecovered';late['expectation']='violates_limits'
    for m,delay in zip(late['study']['induction']['motors'],[.9,1.2],strict=True):m['contactor']['pickup_delay_s']=delay
    cases.append(late)
    return dict(id='dol-restart-energy-three-grid',purpose='regression',cases=cases,total_timeout_s=300)


def run(out):
    out=Path(out)
    if out.exists():raise ValueError('new acceptance directory required')
    out.mkdir(parents=True);document=example();actual=run_engineering(document,out/'engineering')
    checked=verify_engineering(out/'engineering');rows=[]
    for case,row in zip(document['cases'],actual['cases'],strict=True):
        path=out/'engineering'/case['id']/'grid-2'
        s=StudySpec.model_validate(read_json(path/'study.json'));result=read_json(path/'result.json')
        if any(m.thermal for m in s.induction.motors):
            ref=thermal_reference(s,result);keys=['speed_pu','thermal_level','current_pu','bus_voltage_pu']
            limits=dict(zip(keys,[2e-5,2e-5,1e-4,1e-5]));motor=s.induction.motors[0]
            errors={motor.id:{k:float(max(abs(np.array(result['channels'][motor.id+'.'+k])-ref['values'][:,j]))) for j,k in enumerate(keys)}}
            trip=next(e['time_s'] for e in result['event_log'] if e['kind']=='thermal_trip')
            clock_error=abs(trip-ref['trip_time_s']);clock_ok=clock_error<=1e-6
            reference=dict(time_s=result['time_s'],sample_side=result['sample_side'],values=ref['values'].tolist(),keys=keys,trip_time_s=ref['trip_time_s'])
            currents={motor.id:ref['values'][:,2]}
        else:
            oracle=s.model_copy(deep=True);expected=[]
            for m in oracle.induction.motors:
                if m.contactor:
                    p=m.contactor;opened=.2+p.dropout_delay_s;closed=max(.5+p.pickup_delay_s,opened+p.minimum_off_time_s)
                    expected.append(dict(time_s=opened,kind='motor_open',equipment_id=m.id))
                    if closed<oracle.induction.duration_s:expected.append(dict(time_s=closed,kind='motor_close',equipment_id=m.id))
                    m.contactor=None
            expected.sort(key=lambda e:e['time_s'])
            if expected:
                assert [(e.time_s,e.value) for e in s.induction.events]==[(.2,.5),(.5,1.)]
                oracle.induction.events.extend(InductionEvent.model_validate(e) for e in expected)
                oracle.induction.events.sort(key=lambda e:e.time_s)
            got=[e for e in result['event_log'] if e.get('cause') in {'undervoltage_timer','automatic_restart'}]
            clock_ok=len(got)==len(expected) and all((a['kind'],a['equipment_id'])==(b['kind'],b['equipment_id']) and abs(a['time_s']-b['time_s'])<=1e-8 for a,b in zip(got,expected))
            ref=rotor_flux_group_reference(oracle,result);limits=LIMITS
            errors={m:{k:float(max(abs(np.array(result['channels'][m+'.'+k])-values[:,j]))) for j,k in enumerate(KEYS)} for m,values in ref.items()}
            clock_error=max([abs(a['time_s']-b['time_s']) for a,b in zip(got,expected)]+[0.])
            reference=dict(time_s=result['time_s'],sample_side=result['sample_side'],channels={m:dict(zip(KEYS,v.T.tolist())) for m,v in ref.items()},expected_actions=expected)
            currents={m:v[:,3] for m,v in ref.items()}
        reference_path=out/'reference'/case['id'];reference_path.mkdir(parents=True)
        (reference_path/'trace.json').write_bytes(encoded(reference))
        audit=read_json(path/'audit.json');i2t_errors={}
        dt=np.diff(result['time_s'])
        for identifier,current in currents.items():
            # Separate midpoint+slope expression for the SI current interpolant.
            integral=float(sum(dt*((current[:-1]+current[1:])**2/4+(current[1:]-current[:-1])**2/12)))
            reported=audit['motors'][identifier]['metrics']['i2t_pu2_s']
            i2t_errors[identifier]=abs(reported-integral)/max(abs(integral),1e-9)
        passed=clock_ok and all(v<=limits[k] for group in errors.values() for k,v in group.items()) and all(v<=1e-5 for v in i2t_errors.values())
        rows.append(dict(id=case['id'],passed=passed,clock_error_s=clock_error,maximum_errors=errors,limits=limits,
                         i2t_si_relative_errors=i2t_errors,i2t_si_relative_limit=1e-5,
                         numerically_qualified=row['numerically_qualified'],engineering_acceptable=row['engineering_acceptable']))
    summary=dict(passed=bool(actual['validation_passed'] and all(r['passed'] for r in rows)),cases=rows,
                 engineering_manifest_sha256=checked['manifest_sha256'],independent_reference='physical SI rotor-flux circuit, DOP853; separate thermal root; input-derived contactor clocks',
                 reference_sources={name:digest((ROOT/name).read_bytes()) for name in ['benchmarks/induction_reference.py','benchmarks/thermal_motor_reference.py','benchmarks/motor_engineering_acceptance.py']},
                 field_validated=False,universal_industrial_qualification=False)
    (out/'summary.json').write_bytes(encoded(summary));seal(out);return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);args=p.parse_args()
    r=run(args.out);print(encoded(r).decode());raise SystemExit(0 if r['passed'] else 1)
