"""Cross-check joint machine dynamics using physical SI rotor flux states."""
import pytest
from benchmarks.joint_double_cage_reference import fixture,compare,NAMES
from benchmarks.genrou_coupled_reference import reference


@pytest.mark.parametrize('name',NAMES)
def test_joint_genrou_and_active_double_cage_match_independent_si(name):
    result=compare(fixture(name))
    assert result['passed'],result
    assert not result['field_validated'] and not result['industrial_profile_qualified']


def test_reference_rejects_unknown_fixture():
    with pytest.raises(ValueError,match='unknown'):fixture('arbitrary')


def test_reference_rejects_topology_events_instead_of_treating_them_as_close():
    s=fixture('loaded_sag');data=s.model_dump(mode='json')
    data['coupled']['events']=[dict(time_s=.2,kind='generator_open',equipment_id='g')]
    s=type(s).model_validate(data)
    with pytest.raises(ValueError,match='unsupported event'):reference(s,{})
