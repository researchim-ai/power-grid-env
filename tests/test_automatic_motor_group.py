import pytest
from benchmarks.automatic_motor_group import NAMES,compare,example,rejected_case


@pytest.mark.parametrize('name',NAMES)
def test_independent_closed_loop_group(name):
    r=compare(example(name))
    assert r['passed'],r
    assert not r['field_validated']
    assert not r['native_group_simulator_qualified']
    if name=='grid_scale32':assert r['motor_count']==32
    if name=='generator_scale16':assert r['motor_count']==16
    if 'thermal_tie' in name:
        trips=[e['time_s'] for e in r['expected_events'] if e['kind']=='thermal_trip']
        assert len(trips)>=2 and trips[0]==trips[1]
    if name=='grid_thermal_near':
        trips=[e['time_s'] for e in r['expected_events'] if e['kind']=='thermal_trip']
        assert 0 < trips[1]-trips[0] < 1e-6


def test_cold_group_static_q_limit_independently_rejected():
    assert rejected_case()['passed']
