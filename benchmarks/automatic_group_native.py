"""Mixed motor groups in native ANDES, driven by independent SI decisions.

The native machines and common electrical network are independent. Commanding
stator switches from SI decisions is NOT a native closed-loop controller test.
"""
from pathlib import Path
import argparse,math
import numpy as np
from benchmarks.automatic_motor_group import NAMES,example,signature
from benchmarks.double_cage_reference import reference
from benchmarks.independent_group_contactor import run as hybrid
from benchmarks.group_si_generator import Generator
from benchmarks.andes_group_motor_reference import native_reference
from benchmarks.andes_joint_double_cage import sample_native
from benchmarks.andes_motor_reference import LIMITS
from benchmarks.automatic_motor_native import event_alignment
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint

STEPS=(.00025,.000125,.0000625)


def command_oracle(spec,independent_events):
    d=spec.model_dump(mode='json');key='coupled' if spec.coupled else 'induction';settings=spec.coupled if spec.coupled else spec.induction
    for m in d[key]['motors']:m['contactor']=None;m['thermal']=None
    authored=settings.events;source=spec.network.sources[0].id;commands=[];markers=[]
    def canonical(t):
        matches=[e.time_s for e in authored if abs(e.time_s-t)<=32*np.finfo(float).eps*max(1.,abs(t))]
        return matches[0] if matches else float(t)
    for e in authored:
        if e.kind not in {'motor_open','motor_close'}:commands.append(e.model_dump(mode='json'))
    for e in independent_events:
        if e['kind']=='thermal_trip' or (e['kind'] in {'motor_open','motor_close'} and e.get('outcome') in {'applied','already_in_requested_state'}):
            commands.append(dict(time_s=canonical(e['time_s']),kind='motor_open' if e['kind']=='thermal_trip' else e['kind'],equipment_id=e['equipment_id'],value=None))
    instants=sorted({canonical(e['time_s']) for e in independent_events}|{e.time_s for e in authored})
    for t in instants:
        if not any(e['time_s']==t for e in commands):
            scale=1.
            for e in sorted(authored,key=lambda x:x.time_s):
                if e.kind=='source_voltage' and e.time_s<=t:scale=e.value
            commands.append(dict(time_s=t,kind='source_voltage',equipment_id=source,value=scale));markers.append(t)
    d[key]['events']=sorted(commands,key=lambda e:(e['time_s'],e['kind'] in {'motor_open','motor_close'}));d['id']=spec.id+'-independent-native-commands'
    return StudySpec.model_validate(d),dict(independent_SI_commands=True,production_decisions_used=False,all_native_thermal_and_contactors_removed=True,thermal_replica_has_no_electrical_temperature_feedback=True,noop_source_voltage_sampling_markers_s=markers,event_times_s=instants)


def compare(spec,steps=STEPS,out=None):
    settings=spec.coupled if spec.coupled else spec.induction
    if len(settings.motors)<2:raise ValueError('native group comparison requires multiple motors')
    if len(steps)<2 or list(steps)!=sorted(set(steps),reverse=True) or any(not math.isfinite(h) or not 1e-5<=h<=.01 for h in steps):raise ValueError('strictly decreasing native steps required')
    actual=run_study(spec,isolate=True,wall_timeout_s=90.)
    if not actual.get('completed'):raise ValueError('production group incomplete: '+str(actual.get('status')))
    independent=reference(spec,actual,hybrid_runner=hybrid,generator_factory=Generator if spec.coupled else None)
    expected=[e for e in independent['independent_event_log'] if signature(e) is not None];observed=[e for e in actual['event_log'] if signature(e) is not None]
    if [signature(e) for e in expected]!=[signature(e) for e in observed] or any(abs(a['time_s']-b['time_s'])>1e-6 for a,b in zip(expected,observed)):raise ValueError('independent group control decisions/times mismatch')
    oracle,provenance=command_oracle(spec,independent['independent_event_log']);alignment=event_alignment(actual,provenance['event_times_s'])
    sample_times=[alignment.get(t,t) if side in {'pre_event','post_event'} else t for t,side in zip(actual['time_s'],actual['sample_side'])]
    limits={m.id+'.'+key:limit for m in settings.motors for key,limit in LIMITS.items()}
    if spec.coupled:
        g=spec.network.generators[0]
        limits.update({g.id+'.'+key:limit for key,limit in dict(delta=1e-4,omega=1e-5,e1q=1e-3,e1d=1e-3,e2d=1e-3,e2q=1e-3,p_w=1000.,q_var=1000.).items()})
        limits.update({g.id+'.TGOV1.valve_pu':1e-4,g.id+'.SEXS.field_pu':1e-3})
    if out is not None:
        out=Path(out);write_json(out/'study.json',spec.model_dump(mode='json'));write_json(out/'command-study.json',oracle.model_dump(mode='json'));write_json(out/'production.json',actual)
        write_json(out/'independent-control.json',dict(events=independent['independent_event_log'],provenance=provenance,matched_event_samples=[dict(production_time_s=t,independent_time_s=u) for t,u in alignment.items()]))
    rows=[]
    for step in steps:
        native=native_reference(oracle,step)
        if not native.get('completed'):
            rows.append(dict(step_s=step,passed=False,failure=native))
            if out is not None:write_json(out/f'andes-{step}.json',native)
            continue
        if set(native['channels'])!=set(limits):raise ValueError('native group channel inventory mismatch')
        errors={key:float(max(abs(sample_native(native,sample_times,actual['sample_side'],key)-actual['channels'][key]))) for key in limits}
        rows.append(dict(step_s=step,passed=all(np.isfinite(errors[k]) and errors[k]<=v for k,v in limits.items()),errors=errors,native_honest_jacobian=native['native_honest_jacobian'],maximum_accepted_algebraic_residual_pu=native['maximum_accepted_algebraic_residual_pu'],native_steps_executed=native['native_steps_executed'],retained_samples=native['retained_samples']))
        if out is not None:write_json(out/f'andes-{step}.json',native)
    keys=[m.id+'.speed_pu' for m in settings.motors]+([g.id+'.delta'] if spec.coupled else [])
    converged=all('errors' in r for r in rows) and all(rows[-1]['errors'][key]<rows[0]['errors'][key] for key in keys)
    result=dict(case_id=spec.id,passed=bool(rows[-1]['passed'] and converged),motor_count=len(settings.motors),steps=rows,finest_limits=limits,convergence_passed=converged,independent_controller_events_matched=True,
        maximum_event_pair_delta_s=max([abs(a-b) for a,b in alignment.items()],default=0.),event_pair_tolerance_s=1e-6,regular_sample_clock_unchanged=True,
        adapted_native_group_physics_under_independent_SI_commands=True,native_closed_loop_group_controller_qualified=False,field_validated=False,industrial_profile_qualified=False)
    if out is not None:
        result['artifacts_sha256']={p.name:sha256_file(p) for p in out.glob('*.json') if p.name!='comparison.json'};write_json(out/'comparison.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);args=p.parse_args();rows=[]
    write_json(args.out/'summary.json',dict(passed=False,status='running'))
    try:
        for name in NAMES:
            print('starting',name,flush=True);r=compare(example(name),out=args.out/name);rows.append(r);print(name,r['passed'],flush=True)
        paths=['benchmarks/automatic_group_native.py','benchmarks/andes_group_motor_reference.py','benchmarks/automatic_motor_group.py','benchmarks/double_cage_reference.py','benchmarks/independent_group_contactor.py','benchmarks/group_si_generator.py','benchmarks/induction_reference.py','benchmarks/genrou_reference.py','benchmarks/double_cage_andes_limits.py','benchmarks/andes_motor_terminal.py','benchmarks/andes_joint_double_cage.py','benchmarks/andes_motor_reference.py','benchmarks/automatic_motor_native.py']
        summary=dict(passed=all(r['passed'] for r in rows),cases=rows,code_sha256=package_fingerprint(),native_steps_s=list(STEPS),source_sha256={name:sha256_file(Path(name)) for name in paths},case_files_sha256={str(p):sha256_file(p) for p in [Path('cases/studies/automatic_group_'+name+'.json') for name in NAMES]},native_closed_loop_group_controller_qualified=False,production_decisions_used_as_native_commands=False,field_validated=False,industrial_profile_qualified=False)
        write_json(args.out/'summary.json',summary)
    except Exception as e:
        write_json(args.out/'summary.json',dict(passed=False,status='failed',completed_cases=rows,error=str(e)));raise
    if not summary['passed']:raise SystemExit(1)


if __name__=='__main__':main()
