"""External active-machine trajectories, event sides and fail-closed execution."""
import pytest
from benchmarks.andes_joint_double_cage import example,compare,sample_native,NAMES
from benchmarks.andes_joint_motor_reference import scope,native_reference


@pytest.mark.parametrize('name',NAMES)
def test_active_genrou_and_motor5_external_step_refinement(name):
    r=compare(example(name))
    assert r['passed'] and r['convergence_passed'],r
    for row in r['steps']:
        assert row['initial_algebraic_residual_pu']<1e-10
        assert row['initial_generator_controller_rhs']<1e-8
        assert row['seed_voltage_replacement_error_pu']<1e-8
    assert not r['field_validated'] and not r['industrial_profile_qualified']


def test_event_interpolation_keeps_distinct_sides():
    native=dict(time_s=[0.,.2,.2,.4],sample_side=['regular','pre_event','post_event','regular'],channels={'v':[0.,2.,20.,40.]})
    actual=sample_native(native,[.1,.2,.2,.3],['sample','pre_event','post_event','sample'],'v')
    assert actual==pytest.approx([1.,2.,20.,30.])
    with pytest.raises(ValueError,match='ambiguous'):sample_native(native,[.2],['sample'],'v')
    native['sample_side'][1]='regular'
    with pytest.raises(ValueError,match='event side'):sample_native(native,[.2],['pre_event'],'v')


@pytest.mark.parametrize('change',['line_asymmetry','motor_event','line_charging','wrong_generator'])
def test_reference_explicitly_rejects_unrepresented_equipment(change):
    s=example('cold')
    if change=='line_asymmetry':s.network.lines[0].r_ohm[1][1]+=.1
    elif change=='line_charging':s.network.lines[0].b_siemens[0][0]=.001
    elif change=='wrong_generator':s.coupled.machines[0].generator_id='not-g'
    else:
        data=s.model_dump(mode='json');data['coupled']['events']=[dict(time_s=.2,kind='motor_open',equipment_id='m')];s=type(s).model_validate(data)
    with pytest.raises(ValueError):scope(s)


def test_incomplete_native_workers_cannot_pass(monkeypatch):
    monkeypatch.setattr('benchmarks.andes_joint_double_cage.native_reference',lambda *a,**k:dict(completed=False,status='native_worker_failed'))
    s=example('cold');s.coupled.duration_s=.02
    r=compare(s)
    assert not r['passed'] and not r['convergence_passed']
    assert all(not row['passed'] and 'failure' in row for row in r['steps'])


def test_external_worker_timeout_is_bounded_and_not_a_result():
    r=native_reference(example('cold'),.0005,timeout_s=1e-6)
    assert r==dict(completed=False,status='native_timeout')


def test_native_input_budgets_checked_before_spawn():
    for step in [0.,float('nan'),float('inf'),.1]:
        with pytest.raises(ValueError,match='step budget'):native_reference(example('cold'),step)
    with pytest.raises(ValueError,match='decreasing'):compare(example('cold'),steps=[.000125,.0005])


def test_missing_scheduled_event_samples_are_rejected():
    from benchmarks.andes_joint_double_cage import validate_event_samples
    s=example('voltage_sag')
    r=dict(time_s=[0.,s.coupled.duration_s],sample_side=['initial','sample'])
    with pytest.raises(ValueError,match='event sides'):validate_event_samples(r,s.coupled)
