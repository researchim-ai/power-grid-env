import numpy as np
import pytest
from power_grid_env.hybrid_sensitivity import run_initial_sensitivity,TrajectorySensitivity


from benchmarks.hybrid_trajectory_validation import case as study

def final(r):
    c=r['channels'];a=[c['m.'+k][-1] for k in ('e_real_pu','e_imag_pu','speed_pu')]
    if 'm.thermal_level' in c:a.append(c['m.thermal_level'][-1])
    return np.array(a)


@pytest.mark.parametrize('thermal',[False,True])
def test_full_trajectory_matches_independent_central_difference(thermal):
    s=study(thermal);n=4 if thermal else 3;v=np.arange(1,n+1,dtype=float)/n;h=2e-5
    r=run_initial_sensitivity(s.network,s.induction,v)
    assert r['completed'] and r['sensitivity']['available'],r
    # Baseline finite differences run the unobserved primary integrator.
    from power_grid_env.induction_network import InductionNetwork,integrate_induction_model,validate_induction
    from power_grid_env.motor_operating_point import initialize_motor_state
    def physical(sign):
        m=InductionNetwork(s.network,s.induction);x,op=initialize_motor_state(m)
        return integrate_induction_model(m,validate_induction(s.network,s.induction),x+sign*h*v,op)
    a,b=physical(1),physical(-1)
    assert a['completed'] and b['completed']
    signature=lambda z:[(e['kind'],e.get('outcome')) for e in z['event_log']]
    assert signature(a)==signature(b)==signature(r)
    np.testing.assert_allclose(np.array(r['sensitivity']['final_state_directions'])[:,0],(final(a)-final(b))/(2*h),rtol=2e-4,atol=2e-6)
    if thermal:assert any(e['kind']=='thermal_trip' for e in r['event_log'])
    else:assert [e['kind'] for e in r['event_log'][:2]]==['motor_close','motor_open']


@pytest.mark.parametrize('bad',[[],[float('nan')],[[1j]]])
def test_invalid_directions(bad):
    with pytest.raises(ValueError):TrajectorySensitivity(bad)


def test_coincident_guards_do_not_fabricate_gradient():
    observer=TrajectorySensitivity(np.eye(3));observer.before_events(None,.1,np.zeros(3),{0,1},[],False)
    r=observer.report(True)
    assert not r['available'] and r['final_state_directions'] is None
    assert 'coincident' in r['reason']


def test_independent_full_study_audit():
    from power_grid_env.hybrid_sensitivity import audit_initial_sensitivity
    s=study(True);r=audit_initial_sensitivity(s.network,s.induction,[.25,.5,.75,1.])
    assert r['passed'],r
    assert len(r['steps'])==3


def test_direction_budget_and_wrong_size():
    with pytest.raises(ValueError):TrajectorySensitivity(np.ones((3,33)))
    s=study()
    with pytest.raises(ValueError,match='dimension'):run_initial_sensitivity(s.network,s.induction,[1.,2.])


def test_network_dae_physical_coordinates_preserve_thermal_sensitivity():
    s=study(True);directions=np.eye(4)[:,[0,2,3]]
    radau=run_initial_sensitivity(s.network,s.induction,directions)
    s.induction.integration_method='network_dae'
    dae=run_initial_sensitivity(s.network,s.induction,directions)
    assert dae['completed'] and dae['sensitivity']['available'],dae
    np.testing.assert_allclose(dae['sensitivity']['final_state_directions'],radau['sensitivity']['final_state_directions'],rtol=2e-4,atol=2e-6)
    assert [e['kind'] for e in dae['event_log']]==[e['kind'] for e in radau['event_log']]


def test_complex_audit_direction_is_rejected_without_projection():
    from power_grid_env.hybrid_sensitivity import audit_initial_sensitivity
    s=study()
    with pytest.raises(ValueError,match='real'):audit_initial_sensitivity(s.network,s.induction,np.array([1j,0j,0j]))
