"""External Motor3 cold-start DAE comparison with explicit initialization scope."""
from pathlib import Path
import pytest
from power_grid_env.study_spec import StudySpec
from benchmarks.andes_motor_reference import compare,native_reference,scope,STEPS


def example(name='nominal'):
    return StudySpec.model_validate_json(Path('cases/studies/andes_motor_'+name+'.json').read_text())


@pytest.mark.parametrize('name',['nominal','frequency_base'])
def test_cold_start_trajectories_converge_to_external_ANDES_motor(name):
    result=compare(example(name));assert result['passed'],result
    errors=[row['errors']['speed_pu'] for row in result['steps']]
    assert errors[-1]<errors[0]/5.
    assert result['steps'][-1]['initial_algebraic_residual_pu']<1e-10
    assert not result['field_validated']


def test_external_initial_state_and_literal_load_mapping_are_declared():
    r=native_reference(example(),STEPS[-1]);assert r['completed'],r
    assert r['time_s'][0]==0. and r['channels']['speed_pu'][0]==0.
    assert r['channels']['e_real_pu'][0]==r['channels']['e_imag_pu'][0]==0.
    assert r['native_load_mapping_error_pu']<1e-10
    assert 'c2*slip^2' in r['mechanical_mapping']['literal_native_polynomial']
    assert r['native_source_sha256'] and 'no curve shifting' in r['integration']


def test_external_scope_and_resource_failure_are_not_passes():
    from power_grid_env.induction import InductionEvent
    s=example();s.induction.events=[InductionEvent(time_s=.1,kind='motor_open',equipment_id=s.induction.motors[0].id)]
    with pytest.raises(ValueError):scope(s)
    assert native_reference(example(),STEPS[0],timeout_s=.000001)['status']=='native_timeout'
    with pytest.raises(ValueError):compare(example(),steps=[.001,.001])
