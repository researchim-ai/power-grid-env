"""Recompute loaded protection evidence and join the physical initialization gate."""
from pathlib import Path
import copy
from types import SimpleNamespace
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash
from power_grid_env.running_fault_protection import assess
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest
from benchmarks.running_fault_cartesian import CartesianProtocol
from benchmarks.motor_only_loaded_protection import jobs
from benchmarks.motor_only_loaded_acceptance import verify_state
from benchmarks.motor_only_loaded_reference import WindingReference
from benchmarks.running_fault_island_reference import algebra
from benchmarks.closed_loop_protection_reference import compare_solvers
from benchmarks.motor_fault_branch_duty import measured


def qualify(protocol,source,core_source,core_accepted,out):
    p=CartesianProtocol.model_validate(protocol);source=Path(source);core_source=Path(core_source);core_accepted=Path(core_accepted)
    anchor=verify(source/'index');core_anchor=verify(core_source/'index');receipt_anchor=verify(core_accepted)
    core=read_json(core_accepted/'summary.json');stored=read_json(source/'index/summary.json')
    if not core['passed'] or core['code_sha256']!=package_fingerprint() or core['source_index_sha256']!=core_anchor:raise ValueError('loaded physical acceptance/runtime binding differs')
    for path in [source,core_source]:
        if CartesianProtocol.model_validate(read_json(path/'index/protocol.json')).model_dump(mode='json')!=p.model_dump(mode='json'):raise ValueError('loaded protection owner protocol differs')
    if not stored['passed'] or stored['producer_sha256']!=digest(Path('benchmarks/motor_only_loaded_protection.py').read_bytes()) or stored['code_sha256']!=package_fingerprint():raise ValueError('loaded protection producer/outcome differs')
    declared=list(jobs(p));by_id={j['id']:j for j in declared};entries={r['id']:r for r in stored['rows']}
    if len(entries)!=192 or set(entries)!=set(by_id) or {x.name for x in source.iterdir()}!=set(by_id)|{'index'}:raise ValueError('loaded protection coverage/layout differs')
    latest=0.;max_residual=0.;grouped={}
    for j in declared:
        child=source/j['id'];before=verify(child)
        if before!=entries[j['id']]['manifest_sha256'] or read_json(child/'study.json')!=j['study'] or read_json(child/'requirements.json')!=j['requirements']:raise ValueError('loaded protection owner inputs differ')
        study=StudySpec.model_validate(j['study']);report=read_json(child/'trace.json')
        if report.get('settings')!=study.coupled.model_dump(mode='json') or report.get('network_sha256')!=canonical_hash(study.network) or report.get('code_sha256')!=package_fingerprint():raise ValueError('protected trace identity differs')
        verify_state(study,report)
        physical=study.model_dump(mode='json');physical['coupled']['events']=[]
        ref=WindingReference(physical);_,initial=ref.initialize();motor=study.coupled.motors[0]
        if abs(initial['speed_pu']-report['channels'][motor.id+'.speed_pu'][0])>1e-8:raise ValueError('protected prefault state differs from independent T root')
        result=assess(study,report,j['requirements']);oracle=algebra(SimpleNamespace(study=study),report)
        if result!=read_json(child/'result.json') or oracle!=read_json(child/'algebra.json') or not oracle['passed']:raise ValueError('loaded protection audit differs')
        if j['uv']:
            if not result['passed']:raise ValueError('protected loaded case failed')
            latest=max(latest,result['fault_extinction_time_s']-study.coupled.faults[0].apply_s)
        elif result['passed'] or result['checks']['motors_disconnected'] or not result['checks']['clock_audits'] or not result['checks']['grid_electrically_isolated']:
            raise ValueError('matched no-UV negative no longer reproduced')
        max_residual=max(max_residual,result['maximum_sampled_fault_current_after_grid_isolation_a'] or 0.)
        key=(j['condition_id'],j['thermal'],j['kind'],j['uv'],j['method']);grouped.setdefault(key,{})[j['grid']]=measured(j['study'],report)
        if verify(child)!=before:raise ValueError('loaded protected child changed')
    for pair in stored['solver_pairs']:
        j=by_id[pair['id']];other=next(k for k in declared if k['method']=='radau' and all(k[key]==j[key] for key in ['condition_id','thermal','kind','uv','grid']))
        checked=compare_solvers(read_json(source/other['id']/'trace.json'),read_json(source/j['id']/'trace.json'))
        if dict(id=j['id'],**checked)!=pair or not checked['passed']:raise ValueError('protected solver pair differs')
    fine=[]
    for key,grids in grouped.items():
        if set(grids)!={0,1,2}:raise ValueError('protected duty grids incomplete')
        change=max(abs(grids[2][k]['i2t_a2s']-grids[1][k]['i2t_a2s'])/max(grids[2][k]['i2t_a2s'],1e-9) for k in grids[2])
        fine.append(dict(key=list(key),maximum_relative_i2t_change=change,passed=change<=.002))
    if not all(x['passed'] for x in fine):raise ValueError('protected duty output-grid agreement failed')
    if verify(source/'index')!=anchor or verify(core_source/'index')!=core_anchor or verify(core_accepted)!=receipt_anchor:raise ValueError('loaded acceptance sources changed')
    result=dict(passed=True,code_sha256=package_fingerprint(),core_physics_acceptance_sha256=receipt_anchor,core_index_sha256=core_anchor,
        protected_index_sha256=anchor,total_native_qualification_runs=512,accepted_positive_runs=456,
        initialization_negative_runs=8,no_uv_negative_runs=48,independent_loaded_trajectories=48,independent_native_comparisons=96,
        independent_programmed_fault_trajectories=16,solver_pairs=252,protected_fault_duty_runs=192,protected_duty_fine_pairs=64,
        latest_protected_fault_extinction_delay_s=latest,largest_sampled_motor_fed_current_after_grid_isolation_a=max_residual,
        duty=fine,independent_active_protection_qualified=False,normative_approval=False,full_DES04_complete=False,reward=None,
        producer_sha256=digest(Path(__file__).read_bytes()),
        scope='Loaded machine/network initialization and independent programmed-fault physics joined with144 protected loaded positives and48 matched no-UV negatives. Roots, original equations, SI algebra, relay clocks, actuator actions, solver pairs and sampled I²t checked. Active protection trajectories are not independently integrated.')
    root=Path(out)
    if any(root.resolve().is_relative_to(p.resolve()) for p in [source,core_source,core_accepted]):raise ValueError('loaded acceptance output must be outside sources')
    root.mkdir(parents=True,exist_ok=False);(root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['protocol','source','core-source','core-accepted','out']:p.add_argument('--'+key,required=True)
    a=vars(p.parse_args());a['protocol']=read_json(a['protocol']);r=qualify(**a);print(dict(passed=r['passed'],total_native_runs=512))
