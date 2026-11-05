import pytest
from benchmarks.network_automation_reference import NAMES,example,reference,compare
from power_grid_env.study_spec import run_study

@pytest.mark.parametrize('name',NAMES)
def test_automation_matches_independent_SI_dynamics_and_prescribed_timelines(name):
    spec=example(name);ref=reference(name,spec);primary=run_study(spec)
    result=compare(primary,ref);assert result['passed'],result

@pytest.mark.parametrize('defect',['extra_trip','missing_action','nonfinite','missing_sample'])
def test_automation_reference_fails_closed(defect):
    spec=example('overcurrent');ref=reference('overcurrent',spec);primary=run_study(spec)
    if defect=='extra_trip':primary['event_log'].append(dict(kind='network_relay_trip',equipment_id='unexpected',time_s=.3))
    elif defect=='missing_action':primary['event_log']=[e for e in primary['event_log'] if e['kind']!='network_relay_trip']
    elif defect=='nonfinite':primary['channels']['g.omega'][0]=float('nan')
    else:primary['time_s'][0]=.00001
    if defect in {'extra_trip','missing_action'}:assert not compare(primary,ref)['passed']
    else:
        with pytest.raises(ValueError):compare(primary,ref)
