"""Root-time cohorts, event-side sensitivity and deterministic phase contracts."""
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from power_grid_env.hybrid_events import coincident_roots,saltation_product,PHASE_ORDER


def guards_at(times,direction=1):
    result=[]
    for target in times:
        def guard(t,x,target=target):return t-target
        guard.terminal=True;guard.direction=direction;result.append(guard)
    return result


@pytest.mark.parametrize('gap',[0.,1e-15,1e-9])
def test_independently_located_root_cohort_has_ulp_time_bound(gap):
    guards=guards_at([.2,.2+gap]);r=solve_ivp(lambda t,x:np.ones(1),(0.,1.),[0.],events=guards,dense_output=True,max_step=.5)
    indices,audit=coincident_roots(r,guards)
    assert indices==({0,1} if gap<1e-14 else {0})
    assert audit['maximum_snap_s']<=audit['time_tolerance_s'] and audit['phase_order']==list(PHASE_ORDER)


def test_flat_zero_is_not_invented_as_an_independent_coincidence():
    g=guards_at([.2])[0]
    r=solve_ivp(lambda t,x:np.ones(1),(0.,1.),[0.],events=[g],dense_output=True)
    def flat(t,x):return 0.
    flat.direction=1
    indices,_=coincident_roots(r,[g,flat]);assert indices=={0}


@pytest.mark.parametrize('reset_scale',[1.,.6])
def test_saltation_matches_exact_piecewise_event_time_derivative(reset_scale):
    fm=np.array([1.,2.]);fp=np.array([3.,4.]);normal=np.array([1.,0.]);v=np.array([.3,-.4]);x=np.array([0.,0.]);eps=1e-6
    def flow(z):
        event=(1-z[0])/fm[0];return reset_scale*(z+fm*event)+fp*(2-event)
    fd=(flow(x+eps*v)-flow(x-eps*v))/(2*eps)
    actual=saltation_product(normal,fm,fp,v,reset_product=lambda z:reset_scale*z)
    assert np.allclose(actual,fd,atol=1e-9)


def test_time_dependent_reset_and_guard_formula():
    n=np.array([1.,0.]);fm=np.array([2.,1.]);fp=np.array([1.,3.]);v=np.array([.3,.2]);rt=np.array([.4,.1])
    expected=v+(fp-fm-rt)*(n@v)/(n@fm+.5)
    assert np.allclose(saltation_product(n,fm,fp,v,reset_time_derivative=rt,guard_time_derivative=.5),expected)


@pytest.mark.parametrize('normal,fm',[(np.zeros(2),np.ones(2)),(np.array([1.,0.]),np.array([0.,1.]))])
def test_grazing_and_undefined_saltation_rejected(normal,fm):
    with pytest.raises(ValueError,match='transversal'):saltation_product(normal,fm,np.ones(2),np.ones(2))


def test_new_policy_thermal_trip_precedes_same_time_close():
    from pathlib import Path
    from power_grid_env.study_spec import StudySpec,run_study
    s=StudySpec.model_validate_json(Path('cases/studies/thermal_blocked_reclose.json').read_text());s.induction.event_policy='coincident_roots_v1'
    s.induction.events=[e for e in s.induction.events if e.kind!='motor_open']
    r=run_study(s);assert r['completed'],r
    assert next(e for e in r['event_log'] if e['kind']=='motor_close')['outcome']=='blocked_thermal_latch'
    assert r['hybrid_event_audit']['cohorts'] and all(a['maximum_snap_s']<=a['time_tolerance_s'] for a in r['hybrid_event_audit']['cohorts'])
