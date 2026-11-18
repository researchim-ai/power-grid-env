import copy
import numpy as np
import pytest
from benchmarks.generator_trip_cases import example
from benchmarks.generator_trip_reference import GeneratorTripReference
from benchmarks.generator_trip_acceptance import compare,coverage
from power_grid_env.study_spec import StudySpec,run_study

@pytest.mark.parametrize('controls',['governor','exciter','both'])
@pytest.mark.parametrize('mode',['passport','equilibrium'])
@pytest.mark.parametrize('method',['radau','network_dae'])
def test_trip_time_and_complete_posttrip_trajectory(controls,mode,method):
    s=example(controls,mode);s['coupled']['integration_method']=method;r=run_study(StudySpec.model_validate(s));ref=GeneratorTripReference(s).run()
    result=compare(ref,r);assert r['completed'] and result['passed'],{k:v for k,v in result.items() if k!='values'}
    coverage(r['event_log'],controls)
    assert r['channels']['g.current_pu'][-1]==r['channels']['g.connected'][-1]==0.
    trip=next(e['time_s'] for e in ref.events if e['kind']=='generator_thermal_trip')
    assert trip<.1 if mode=='passport' else .1<trip<.2


def test_posttrip_replica_is_exact_exponential_cooling():
    r=GeneratorTripReference(example()).run();trip=next(e['time_s'] for e in r.events if e['kind']=='generator_thermal_trip')
    for t in np.linspace(trip,.4,21):
        values=r.sample(t,'post_event')
        assert values['g.stator_thermal_level']==pytest.approx(r.replica.trip_level*np.exp(-(t-trip)/r.replica.time_constant_s),abs=1e-8)
        assert values['g.current_pu']==values['g.p_w']==values['g.q_var']==0.
    pre=r.sample(trip,'pre_event');post=r.sample(trip,'post_event')
    for k in ['g.delta','g.omega','m.speed_pu','m.stator_temperature_c','m.rotor_temperature_c','g.stator_thermal_level']:
        assert pre[k]==pytest.approx(post[k],abs=1e-12)
    assert pre['g.connected']==1. and post['g.connected']==0.


def test_posttrip_SEXS_uses_machine_terminal_not_grid_voltage():
    r=GeneratorTripReference(example()).run();x,c=r.state_at(.3)
    v=r.sample(.3);expected=r.vref-abs(r.generator_emf(x))/r.gv
    assert v['g.SEXS.error_pu']==pytest.approx(expected,abs=1e-12)
    assert abs(expected-(r.vref-v['g.bus_voltage_pu']))>1e-3


def test_independent_trip_does_not_call_native_thermal_or_topology_helpers(monkeypatch):
    from power_grid_env.generator_thermal import GeneratorThermalBank
    from power_grid_env.coupled_rms import CoupledNetwork
    def forbidden(*a,**k):raise AssertionError('native helper used')
    for name in ['initialize','guards','apply','derivatives']:monkeypatch.setattr(GeneratorThermalBank,name,forbidden)
    monkeypatch.setattr(CoupledNetwork,'generator_currents',forbidden)
    coverage(GeneratorTripReference(example()).run().events,'both')


def test_consumer_rejects_trip_outcome_tampering():
    s=example();r=run_study(StudySpec.model_validate(s));ref=GeneratorTripReference(s).run()
    wrong=copy.deepcopy(r);next(e for e in wrong['event_log'] if e['kind']=='generator_thermal_trip')['outcome']='ignored'
    assert not compare(ref,wrong)['passed']
