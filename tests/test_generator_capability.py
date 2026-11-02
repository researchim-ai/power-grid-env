"""Independent quadrature, inverse constraints and cross-check against the dynamic solver."""
import math

import pytest
from scipy.integrate import solve_ivp

from power_grid_env.generator_capability import (
    CurrentDuty, ThermalDutyStudy, cooling_time, current_ceiling, evaluate,
    thermal_level, time_to_trip,
)


@pytest.mark.parametrize('level,current,tau,duration', [
    (0., 2., 10., .1), (.8, .1, 3., 12.), (.5, 1., .2, 1.),
    (1., 1., 4., 10.), (0., 0., 1., 1.), (.2, 3., 20., 30.),
])
def test_closed_form_matches_independent_ode(level,current,tau,duration):
    r=solve_ivp(lambda t,x:[(current**2-x[0])/tau],(0,duration),[level],rtol=1e-11,atol=1e-13)
    assert thermal_level(level,current,duration,tau)==pytest.approx(r.y[0,-1],abs=2e-10)


@pytest.mark.parametrize('duration',[1e-9,.01,1.,100.,1e6])
def test_current_ceiling_is_inverse_of_thermal_evolution(duration):
    ceiling=current_ceiling(.2,duration,10.,1.)
    assert thermal_level(.2,ceiling,duration,10.)==pytest.approx(1.,abs=1e-14)
    assert thermal_level(.2,ceiling*.999,duration,10.)<1.
    assert thermal_level(.2,ceiling*1.001,duration,10.)>1.


def test_trip_time_and_cooling_inverse_with_residual_load():
    hit=time_to_trip(.2,2.,10.,1.)
    assert thermal_level(.2,2.,hit,10.)==pytest.approx(1.)
    cool=cooling_time(1.,.3,10.,.2)
    assert thermal_level(1.,.2,cool,10.)==pytest.approx(.3)
    assert cooling_time(1.,.04,10.,.2) is None
    assert cooling_time(1.,0.,10.) is None
    assert cooling_time(.2,.3,10.)==0.
    assert time_to_trip(.2,1.,10.,1.) is None
    assert time_to_trip(1.,0.,10.,1.)==0.
    assert current_ceiling(1.,1.,10.,1.) is None


def test_short_pulse_does_not_lose_heat_to_cancellation():
    assert thermal_level(0.,1.,1e-18,1.)==pytest.approx(1e-18,rel=1e-14,abs=0)


def study(segments,initial=0.):
    return ThermalDutyStudy(protection=dict(generator_id='g',time_constant_s=1.,trip_level=1.),
                            initial_level=initial,segments=segments)


def test_trip_cannot_be_hidden_by_later_cooling():
    r=evaluate(study([dict(duration_s=1.,current_pu=2.),dict(duration_s=20.,current_pu=0.)]))
    assert not r['admissible'] and r['final_level']<1.
    assert r['first_trip_s']==pytest.approx(math.log(4/3))
    assert not r['automatic_reclose_permitted']


def test_segmentation_does_not_change_duty_result():
    a=evaluate(study([dict(duration_s=1.,current_pu=.7)]))
    b=evaluate(study([dict(duration_s=.01,current_pu=.7)]*100))
    assert a['final_level']==pytest.approx(b['final_level'],abs=1e-14)
    assert a['admissible'] and b['admissible']


@pytest.mark.parametrize('bad',[-1.,float('inf'),float('nan')])
def test_nonphysical_inputs_rejected(bad):
    with pytest.raises(ValueError):thermal_level(bad,1.,1.,1.)
    with pytest.raises(ValueError):time_to_trip(0.,bad,1.,1.)
    with pytest.raises(ValueError):current_ceiling(0.,bad,1.,1.)
    with pytest.raises(ValueError):cooling_time(1.,bad,1.)
    with pytest.raises(ValueError):CurrentDuty(duration_s=1.,current_pu=bad)


def test_dynamic_generator_trip_matches_planning_time():
    from benchmarks.generator_thermal_cases import example
    from power_grid_env.study_spec import run_study
    s=example('constant_cold')
    r=run_study(s,isolate=True,wall_timeout_s=90.)
    assert r['completed']
    p=s.coupled.generator_thermal[0]
    predicted=time_to_trip(p.initial_level,r['channels']['g2.current_pu'][0],p.time_constant_s,p.trip_level)
    actual=next(e['time_s'] for e in r['event_log'] if e['kind']=='generator_thermal_trip')
    assert actual==pytest.approx(predicted,abs=1e-8)


def test_long_cooling_retains_small_nonzero_equilibrium():
    assert thermal_level(1e20,1e-5,1000.,1.)==pytest.approx(1e-10,rel=1e-14)


def test_cli_writes_strict_json_and_rejects_invalid_input(tmp_path):
    import json,subprocess,sys
    src=tmp_path/'study.json';out=tmp_path/'result.json'
    src.write_text(study([dict(duration_s=.1,current_pu=2.)]).model_dump_json())
    r=subprocess.run([sys.executable,'-m','power_grid_env.generator_capability',str(src),'--out',str(out)],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    assert json.loads(out.read_text())['admissible']
    out.unlink();src.write_text('{}')
    r=subprocess.run([sys.executable,'-m','power_grid_env.generator_capability',str(src),'--out',str(out)],capture_output=True,text=True)
    assert r.returncode!=0 and not out.exists()


def test_initial_violation_stays_inadmissible_after_cooling():
    r=evaluate(study([dict(duration_s=10.,current_pu=0.)],initial=1.))
    assert not r['admissible'] and r['first_trip_s']==0.


def test_extreme_finite_cooling_avoids_intermediate_ratio_overflow():
    t=cooling_time(1e300,1e-300,1.)
    assert math.isfinite(t) and t==pytest.approx(600*math.log(10))


def test_unrepresentable_time_is_rejected():
    with pytest.raises(ValueError,match='floating point'):
        cooling_time(1e300,1e-300,1e308)
