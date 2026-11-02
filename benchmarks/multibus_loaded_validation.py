"""Loaded multibus motor/GENROU acceptance with independent initialization."""
from pathlib import Path
import argparse,json,math
import numpy as np
from benchmarks.multibus_loaded_cases import NAMES,NEGATIVE
from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.andes_multibus_motor import native_reference
from benchmarks.multibus_motor_validation import compare_si,compare_trace
from benchmarks.native_online_validation import STEPS,source_identity,qualify,normalized_error,channel_limits
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint

ROOT=Path(__file__).resolve().parents[1]


def native_timeout(name):
    # Measured isolated 48k-step generator run: ~118 s on this host.
    # Keep an explicit bounded cross-host margin for the external oracle.
    return 240. if name=='gen_cold_start' else 120.


def load_case(name):
    if name not in (*NAMES,*NEGATIVE):raise ValueError('unknown loaded corpus case')
    return StudySpec.model_validate_json((ROOT/'cases/studies'/('multibus_loaded_'+name+'.json')).read_text())


def drift_limit(key):
    if key.endswith(('.p_w','.q_var')):return .1
    if key.split('.')[-1] in ('connected','thermal_latched','contactor_voltage_available','contactor_pending_restart','contactor_restart_count','contactor_timer_active'):return 0.
    return 1e-8


def stationary_check(spec,traces):
    if (spec.coupled if spec.coupled else spec.induction).events:raise ValueError('stationary check requires no authored events')
    results={}
    for label,trace in traces.items():
        errors={}
        for key in channel_limits(spec):
            values=np.asarray(trace['channels'][key],dtype=float)
            if not len(values) or not np.isfinite(values).all():raise ValueError('invalid stationary channel')
            errors[key]=float(np.max(abs(values-values[0])))
        bounds={k:drift_limit(k) for k in errors}
        results[label]=dict(passed=all(errors[k]<=v for k,v in bounds.items()),maximum_drifts=errors,limits=bounds)
    return dict(passed=all(r['passed'] for r in results.values()),traces=results)


def runup_check(settings,primary,independent,native):
    if not native.get('completed'):
        return dict(runup_passed=False,runup_failure='incomplete_native_run')
    speeds={label:{m.id:trace['channels'][m.id+'.speed_pu'][-1] for m in settings.motors} for label,trace in [('production',primary),('SI',independent),('native_fine',native)]}
    return dict(final_speeds_pu=speeds,runup_passed=all(math.isfinite(v) and v>.95 for values in speeds.values() for v in values.values()))


def reject_case(name,folder):
    spec=load_case(name);write_json(folder/'study.json',spec.model_dump(mode='json'))
    primary=run_study(spec,isolate=True,wall_timeout_s=90.);write_json(folder/'production.json',primary)
    reason=None
    try:independent_circuit(spec)
    except ValueError as exc:reason=str(exc)
    native=native_reference(spec,STEPS[0]);write_json(folder/'native.json',native)
    if name in ('generator_q_limit','cold_generator_q_limit'):
        passed=primary.get('completed') is False and primary.get('reason')=='generator_initial_q_limit' and reason is not None and 'initial Q limit' in reason and native.get('completed') is False and 'initial Q limit' in native.get('log_tail','')
    else:
        passed=primary.get('completed') is False and primary.get('reason') in ('no_qualified_operating_point_found','operating_point_search_failed') and reason is not None and 'high-speed' in reason and native.get('completed') is False and 'high-speed' in native.get('log_tail','')
    result=dict(case_id=name,passed=bool(passed),production_reason=primary.get('reason'),independent_reason=reason,native_status=native.get('status'),no_global_equilibrium_absence_claim=True)
    write_json(folder/'comparison.json',result);return result


def run(out,names=NAMES):
    out=Path(out)
    if (out/'protocol.json').exists():raise ValueError('use new loaded multibus output directory')
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('registered unique loaded cases required')
    sources=source_identity();code=package_fingerprint();hashes={n:canonical_hash(load_case(n)) for n in [*names,*NEGATIVE]}
    write_json(out/'protocol.json',dict(schema='power-grid-env.multibus-loaded/v1',cases=list(names),negative_cases=list(NEGATIVE),case_sha256=hashes,source_sha256=sources,code_sha256=code,native_steps_s=list(STEPS),
        channel_limits={n:channel_limits(load_case(n)) for n in names},event_time_limit_s=1e-6,initial_motor_and_generator_rhs_limit=1e-8,
        physical_current_KCL_limit_pu=1e-6,initial_motor_speed_agreement_limit_pu=1e-8,initial_generator_Q_agreement_limit_var=.01,
        native_timeout_s_by_case={n:native_timeout(n) for n in names},
        stationary_channel_drift_limits={n:{k:drift_limit(k) for k in channel_limits(load_case(n))} for n in names if n.endswith('_hold')},
        stationary_refinement='Every step meets bounds and strict stationary drift; no spurious convergence requirement at numerical equilibrium',
        dynamic_refinement='Finest speed/angle error improves versus coarse or <=1e-10; every finest channel meets fixed bounds',
        cold_runup_case='gen_cold_start',cold_runup_final_speed_minimum_pu=.95))
    rows=[];rejected=[];write_json(out/'summary.json',dict(passed=False,status='running'))
    try:
        for name in names:
            spec=load_case(name);folder=out/name;settings=spec.coupled if spec.coupled else spec.induction
            write_json(folder/'study.json',spec.model_dump(mode='json'));natives=[]
            for h in STEPS:
                print('native',name,h,flush=True);native=native_reference(spec,h,timeout_s=native_timeout(name));write_json(folder/f'andes-{h}.json',native);natives.append(native)
            primary=run_study(spec,isolate=True,wall_timeout_s=90.);write_json(folder/'production.json',primary)
            if not primary.get('completed'):raise ValueError('loaded primary failed: '+str(primary.get('reason')))
            circuit=independent_circuit(spec);independent=circuit.run(primary);write_json(folder/'SI.json',independent)
            si=compare_si(spec,primary,independent);write_json(folder/'SI-comparison.json',si)
            steps=[dict(compare_trace(spec,native,primary,independent),step_s=h) for h,native in zip(STEPS,natives)]
            for native in natives:
                if not native.get('completed'):continue
                initial=native['initialization']
                if initial['maximum_generator_initial_rhs']>1e-8 or (settings.operating_point and initial['maximum_motor_initial_rhs']>1e-8):raise ValueError('native initial equilibrium contract failed')
            if name.endswith('_hold'):
                if not all(r['completed'] for r in steps):result=dict(passed=False,reason='incomplete_native_run')
                else:
                    steady=stationary_check(spec,dict(production=primary,SI=independent,**{str(h):n for h,n in zip(STEPS,natives)}))
                    scores=[max(normalized_error(r['errors'],r['limits']),r['maximum_event_time_error_s']/1e-6) for r in steps]
                    result=dict(passed=bool(steady['passed'] and all(v<=1 for v in scores)),stationary=steady,maximum_normalized_errors_by_step=scores)
            else:result=qualify(steps)
            row=dict(case_id=name,steps=steps,**result)
            if name=='gen_cold_start':
                row.update(runup_check(settings,primary,independent,natives[-1]));row['passed']=row['passed'] and row['runup_passed']
            write_json(folder/'comparison.json',row);rows.append(row);print('compared',name,row['passed'],flush=True)
            if sources!=source_identity() or code!=package_fingerprint() or any(canonical_hash(load_case(n))!=h for n,h in hashes.items()):raise ValueError('loaded corpus producer changed')
            write_json(out/'summary.json',dict(passed=False,status='running',cases=rows))
        for name in NEGATIVE:
            rejected.append(reject_case(name,out/'negative'/name));print('rejected',name,rejected[-1]['passed'],flush=True)
        if sources!=source_identity() or code!=package_fingerprint() or any(canonical_hash(load_case(n))!=h for n,h in hashes.items()):raise ValueError('loaded rejection producer changed')
        summary=dict(passed=all(r['passed'] for r in [*rows,*rejected]),status='complete',cases=rows,rejected_cases=rejected,full_registered_corpus=list(names)==list(NAMES),
            new_positive_native_runs=3*len(rows),negative_native_attempts=len(rejected),fresh_production_and_SI_pairs=len(rows),source_sha256=sources,code_sha256=code,
            field_validated=False,industrial_profile_qualified=False,independent_host_replay=False,controller_commands_precomputed=False)
        summary['artifacts_sha256']={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json')) if p!=out/'summary.json'}
        write_json(out/'summary.json',summary);return summary
    except Exception as exc:
        write_json(out/'summary.json',dict(passed=False,status='failed',cases=rows,rejected_cases=rejected,error=str(exc)));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));a=p.parse_args()
    if not run(a.out,a.cases)['passed']:raise SystemExit(1)
