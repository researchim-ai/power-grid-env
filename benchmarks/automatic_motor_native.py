"""Native physical trajectories under independently computed SI control commands.

Controller decisions and thermal trip times come from the independent SI loop,
never production event logs. This is not a native closed-loop controller test.
"""
from pathlib import Path
import argparse,math
import numpy as np
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint
from benchmarks.automatic_coupled_motor import example,event_signature
from benchmarks.genrou_coupled_reference import reference
from benchmarks.independent_contactor import run as independent_hybrid
from benchmarks.andes_joint_motor_reference import native_reference
from benchmarks.andes_joint_double_cage import sample_native
from benchmarks.andes_motor_reference import LIMITS

NAMES=('basic','continuous_voltage','thermal_trip','pickup_deadline_loss')
STEPS=(.00025,.000125,.0000625)


def command_oracle(spec,independent_events):
    """Only authored inputs and independent decisions can construct native input."""
    d=spec.model_dump(mode='json');s=d['coupled'];s['motors'][0]['contactor']=None;s['motors'][0]['thermal']=None
    authored=spec.coupled.events;source=spec.network.sources[0].id;commands=[];markers=[]
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
    s['events']=sorted(commands,key=lambda e:(e['time_s'],e['kind'] in {'motor_open','motor_close'}));d['id']=spec.id+'-independent-command-oracle'
    return StudySpec.model_validate(d),dict(independent_SI_commands=True,production_decisions_used=False,thermal_state_removed_without_electrical_feedback=True,noop_source_voltage_sampling_markers_s=markers,event_times_s=instants)


def event_alignment(actual,oracle_times):
    times=[t for t,side in zip(actual['time_s'],actual['sample_side']) if side=='pre_event']
    if len(times)!=len(oracle_times):raise ValueError('independent/native event group count mismatch')
    if any(abs(a-b)>1e-6 for a,b in zip(times,oracle_times)):raise ValueError('independent event time mismatch exceeds fixed tolerance')
    return dict(zip(times,oracle_times))


def compare(spec,steps=STEPS,out=None):
    if len(steps)<2 or list(steps)!=sorted(set(steps),reverse=True) or any(not math.isfinite(h) or not 1e-5<=h<=.01 for h in steps):raise ValueError('strictly decreasing native command steps required')
    actual=run_study(spec,isolate=True,wall_timeout_s=60.)
    if not actual.get('completed'):raise ValueError('automatic study incomplete')
    independent=reference(spec,actual,hybrid_runner=independent_hybrid)
    expected=[e for e in independent['independent_event_log'] if event_signature(e) is not None]
    observed=[e for e in actual['event_log'] if event_signature(e) is not None]
    if [event_signature(e) for e in expected]!=[event_signature(e) for e in observed] or any(abs(a['time_s']-b['time_s'])>1e-6 for a,b in zip(expected,observed)):raise ValueError('independent control decision/time mismatch')
    oracle,provenance=command_oracle(spec,independent['independent_event_log']);alignment=event_alignment(actual,provenance['event_times_s'])
    sample_times=[alignment.get(t,t) if side in {'pre_event','post_event'} else t for t,side in zip(actual['time_s'],actual['sample_side'])]
    m=spec.coupled.motors[0];g=spec.network.generators[0]
    limits={m.id+'.'+k:v for k,v in LIMITS.items()}
    limits.update({g.id+'.'+k:v for k,v in dict(delta=1e-4,omega=1e-5,e1q=1e-3,e1d=1e-3,e2d=1e-3,e2q=1e-3,p_w=1000.,q_var=1000.).items()})
    limits.update({g.id+'.TGOV1.valve_pu':1e-4,g.id+'.SEXS.field_pu':1e-3})
    if out is not None:
        out=Path(out);write_json(out/'study.json',spec.model_dump(mode='json'));write_json(out/'command-study.json',oracle.model_dump(mode='json'));write_json(out/'production.json',actual)
        write_json(out/'independent-control.json',dict(events=independent['independent_event_log'],provenance=provenance,matched_event_samples=[dict(production_time_s=t,independent_time_s=u) for t,u in alignment.items()]))
    rows=[]
    for step in steps:
        native=native_reference(oracle,step,terminal_adapter=True)
        if not native.get('completed'):rows.append(dict(step_s=step,passed=False,failure=native));continue
        if set(native['channels'])!=set(limits):raise ValueError('native physical channel inventory mismatch')
        errors={k:float(max(abs(sample_native(native,sample_times,actual['sample_side'],k)-actual['channels'][k]))) for k in limits}
        rows.append(dict(step_s=step,errors=errors,passed=all(np.isfinite(errors[k]) and errors[k]<=limit for k,limit in limits.items()),native_honest_jacobian=native['native_honest_jacobian'],maximum_accepted_algebraic_residual_pu=native['maximum_accepted_algebraic_residual_pu']))
        if out is not None:write_json(out/f'andes-{step}.json',native)
    converged=all('errors' in r for r in rows) and all(rows[-1]['errors'][k]<rows[0]['errors'][k] for k in [m.id+'.speed_pu',g.id+'.delta'])
    result=dict(case_id=spec.id,passed=bool(rows[-1]['passed'] and converged),steps=rows,finest_limits=limits,convergence_passed=converged,
        independent_controller_events_matched=True,maximum_event_pair_delta_s=max([abs(a-b) for a,b in alignment.items()],default=0.),event_pair_tolerance_s=1e-6,
        sampling='Only matched discrete event sides are evaluated at their own independent event time; all regular samples stay on the original clock',
        native_closed_loop_controller_qualified=False,adapted_native_physics_under_independent_commands=True,field_validated=False,industrial_profile_qualified=False)
    if out is not None:
        result['artifacts_sha256']={p.name:sha256_file(p) for p in out.glob('*.json') if p.name!='comparison.json'};write_json(out/'comparison.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    write_json(a.out/'summary.json',dict(passed=False,status='running'));rows=[]
    try:
        for name in NAMES:
            row=compare(example(name),out=a.out/name);rows.append(row);print(name,row['passed'],flush=True)
        result=dict(passed=all(r['passed'] for r in rows),cases=rows,code_sha256=package_fingerprint(),native_steps_s=STEPS,
            source_sha256={str(p):sha256_file(p) for p in map(Path,[__file__,'benchmarks/automatic_coupled_motor.py','benchmarks/independent_contactor.py','benchmarks/genrou_coupled_reference.py','benchmarks/andes_joint_motor_reference.py','benchmarks/andes_motor_terminal.py','benchmarks/andes_motor_reference.py','benchmarks/andes_joint_double_cage.py','benchmarks/double_cage_andes_limits.py'])},
            native_closed_loop_controller_qualified=False,adapted_native_physics_under_independent_commands=True,field_validated=False,industrial_profile_qualified=False)
    except Exception as exc:
        write_json(a.out/'summary.json',dict(passed=False,status='failed',error=str(exc)[:2000],cases=rows));raise
    write_json(a.out/'summary.json',result)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
