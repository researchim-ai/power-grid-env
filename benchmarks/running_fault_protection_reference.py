"""Running-machine protection qualification with unaltered negative controls."""
import copy
from pathlib import Path

from power_grid_env.closed_loop_protection import compile_closed_loop
from power_grid_env.study_spec import StudySpec
from power_grid_env.running_fault_protection import run,verify_clearance
from power_grid_env.study_artifacts import encoded,read_json,seal,digest,verify
from benchmarks.closed_loop_protection_reference import algebra,compare_solvers


def recipes(base):
    """Explicit VI settings from owner base; only UV on/off differs in controls."""
    _,pack,_,_=compile_closed_loop(base)
    # Only the three fault cases, each with running prefault motors.
    for case in pack.cases:
        if not case.id.startswith('fault-'):continue
        kind=case.id.removeprefix('fault-')
        for protected in [False,True]:
            for method in ['radau','network_dae']:
                for grid in range(3):
                    s=case.study.model_dump(mode='json');c=s['coupled']
                    c.update(duration_s=3.5,step_s=.002/2**grid,integration_method=method)
                    c['faults'][0].update(apply_s=3.,clear_s=3.3)
                    for e in c['events']:
                        if e['kind']=='relay_measurement_lost':e['time_s']=1.5
                    for m in c['motors']:
                        m['contactor']=dict(sense_node_id=m['node_id'],dropout_voltage_pu=.3,pickup_voltage_pu=.8,
                            dropout_delay_s=.09,pickup_delay_s=.5,minimum_off_time_s=.5,maximum_automatic_restarts=0) if protected else None
                    req=dict(id='running-'+kind,source=dict(reference='owner://running-fault-profile/v1',
                        conditions='Authored 75kVA single-cage motor, 3s run-up; grid and motor-fed fault extinction <=150ms, observed >=20ms before scheduled removal'),
                        fault_id=c['faults'][0]['id'],grid_breaker_id='primary-cb' if kind=='primary' else 'backup-cb',
                        expected_trip_relays=['backup-oc' if kind=='measurement_loss' else 'primary-oc'],
                        allowed_additional_trip_relays=['backup-oc'] if kind=='breaker_failure' else [],
                        maximum_grid_isolation_s=.15,maximum_fault_extinction_s=.15,extinction_current_a=.1,
                        minimum_extinction_observation_s=.02,minimum_prefault_speed_pu=.9,
                        require_motor_disconnection=True,maximum_sample_step_s=.002)
                    yield kind,protected,method,grid,StudySpec.model_validate(s),req


def qualify(base,out):
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    rows=[];reports={};pairs=[]
    for kind,protected,method,grid,study,req in recipes(base):
        name=f'{kind}-'+('uv' if protected else 'no-uv')+f'-{method}-grid{grid}'
        print(name,flush=True)
        result=run(study,req,root/name)
        verified=verify_clearance(root/name,expected_study=study,expected_requirements=req)
        report=read_json(root/name/'trace.json')
        # Independent two-node SI nodal solve also works after line opening:
        # disconnected motor island is still retained in the equations.
        from types import SimpleNamespace
        oracle=algebra(SimpleNamespace(study=study),report)
        if protected:
            accepted=result['passed']
        else:
            # Negative is a physical requirements failure, not a clock/solver
            # failure. Preserve the same deadlines and clearing horizon.
            accepted=(not result['passed'] and result['checks']['prefault_running'] and
                result['checks']['grid_electrically_isolated'] and result['checks']['clock_audits'] and
                not result['checks']['motors_disconnected'])
        rows.append(dict(id=name,protected=protected,case=kind,grid=grid,method=method,
                         expected_outcome_confirmed=accepted,profile_passed=result['passed'],algebra=oracle,
                         manifest_sha256=verified['manifest_sha256']))
        reports[(kind,protected,method,grid)]=report
        if method=='network_dae':
            pairs.append(dict(case=kind,protected=protected,grid=grid,
                **compare_solvers(reports[(kind,protected,'radau',grid)],report)))
    # Positive/negative recipe must differ only by contactor passport, not
    # protective settings, thresholds, events, source or fault schedule.
    recipe_map={}
    for kind,protected,method,grid,study,req in recipes(base):
        data=study.model_dump(mode='json')
        for m in data['coupled']['motors']:m['contactor']=None
        key=(kind,method,grid)
        recipe_map.setdefault(key,[]).append((data,req))
    equal=all(v[0]==v[1] for v in recipe_map.values())
    result=dict(passed=equal and all(r['expected_outcome_confirmed'] and r['algebra']['passed'] for r in rows) and all(p['passed'] for p in pairs),
        native_runs=len(rows),positive_runs=sum(r['protected'] for r in rows),negative_controls=sum(not r['protected'] for r in rows),
        unchanged_requirements_and_network=equal,rows=rows,solver_pairs=pairs,
        producer_sha256=digest(Path(__file__).read_bytes()),
        scope='Running single-cage motor, VI relay clocks, primary/CB failure/measurement loss, UV contactor/no automatic restart. Conditional SI algebra and shared-model Radau/DAE agreement, not an independent ODE or OEM qualification.')
    (root/'base-plan.json').write_bytes(encoded(base));(root/'summary.json').write_bytes(encoded(result));seal(root)
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=qualify(read_json(a.base),a.out);print(dict(passed=r['passed'],native_runs=r['native_runs']))
    if not r['passed']:raise SystemExit(2)
