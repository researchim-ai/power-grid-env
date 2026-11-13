import copy
from pathlib import Path
import pytest
from power_grid_env.closed_loop_protection import run_closed_loop,verify_closed_loop,compile_closed_loop
from power_grid_env.study_artifacts import read_json,encoded,seal
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def packet(tmp_path_factory):
    plan=read_json(ROOT/'cases/inverse-protection/envelope.json')['base']
    out=tmp_path_factory.mktemp('inverse')/'study'
    result=run_closed_loop(plan,out)
    return out,plan,result


def test_actual_inverse_replay_and_independent_measurements(packet):
    from benchmarks.closed_loop_protection_reference import algebra
    out,plan,r=packet
    assert r['passes_declared_profile'] and r['numerically_qualified']
    assert verify_closed_loop(out,expected_plan=plan)['verified']
    _,pack,_,_=compile_closed_loop(plan)
    for case in pack.cases:
        for level in range(3):
            assert algebra(case,read_json(out/'studies'/case.id/f'grid-{level}'/'result.json'))['passed']
    for row in r['actions']:
        for grid in row['grids']:
            assert all(c['passed'] for c in grid['clock_audits'].values())


def test_forged_progress_after_resealing_cannot_pass(packet,tmp_path):
    import shutil
    out,_,_=packet;target=tmp_path/'copy';shutil.copytree(out,target)
    path=target/'studies/fault-primary/grid-0/result.json'
    r=read_json(path);r['channels']['primary-oc.progress']=[0.]*len(r['time_s'])
    path.write_bytes(encoded(r));(target/'studies/manifest.json').unlink();seal(target/'studies')
    (target/'manifest.json').unlink();seal(target)
    with pytest.raises(ValueError):verify_closed_loop(target)


def test_si_oracle_rejects_forged_measurement(packet):
    from benchmarks.closed_loop_protection_reference import algebra
    out,plan,_=packet;_,pack,_,_=compile_closed_loop(plan)
    case=next(c for c in pack.cases if c.id=='fault-primary')
    r=read_json(out/'studies/fault-primary/grid-0/result.json')
    r['channels']['primary-oc.measurement']=[0.]*len(r['time_s'])
    assert not algebra(case,r)['passed']


def test_event_alignment_allows_only_bounded_one_sided_time_error():
    from benchmarks.closed_loop_protection_reference import compare_solvers
    a=dict(time_s=[0,.1,.1,.2],sample_side=['initial','pre_event','post_event','regular'],channels={'m.speed_pu':[0,.2,.2,.3]})
    b=copy.deepcopy(a);b['time_s'][1]+=1e-9;b['time_s'][2]+=1e-9
    assert compare_solvers(a,b)['passed']
    b['time_s'][1]+=.001
    assert not compare_solvers(a,b)['passed']
