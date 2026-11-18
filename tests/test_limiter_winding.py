"""Independent hybrid references for bounded two-sided controller pulses."""
import copy
import numpy as np
import pytest
from benchmarks.limiter_winding_cases import example
from benchmarks.limiter_winding_reference import LimiterReference
from benchmarks.limiter_winding_acceptance import compare,coverage,event_key
from power_grid_env.study_spec import StudySpec,run_study


@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('controls',['governor','exciter','both'])
def test_two_sided_entries_releases_and_waveforms(method,controls):
    s=example(controls);s['coupled']['integration_method']=method
    native=run_study(StudySpec.model_validate(s));reference=LimiterReference(s).run()
    result=compare(reference,native)
    assert native['completed'] and result['passed'],{k:v for k,v in result.items() if k!='values'}
    coverage(native['event_log'],controls)
    assert native['additional_validation']['ok']


def test_reference_holds_exact_bounds_and_releases_later_without_command():
    r=LimiterReference(example()).run();held_segments=0
    for left,right,sol,c in r.segments:
        for a,side in c['held'].items():
            target=r.entries[a][1 if side=='lower' else 2]
            np.testing.assert_allclose([sol(t)[a] for t in np.linspace(left,right,7)],target,rtol=0,atol=1e-12);held_segments+=1
    assert held_segments>=4
    releases=[e for e in r.events if e['kind']=='controller_limit_release' and e['controller']=='SEXS']
    assert releases[-1]['time_s']>.35 and releases[-1]['time_s']<.5
    assert all(e['time_s']!=releases[-1]['time_s'] for e in r.events if e['kind'].endswith('_step'))


def test_reference_keeps_historical_controller_references_in_context():
    r=LimiterReference(example()).run();before=[r.sample(t) for t in [.01,.1,.2,.3,.4]]
    r.pref=100.;r.vref=200.
    assert before==[r.sample(t) for t in [.01,.1,.2,.3,.4]]
    assert before[1]['g.TGOV1.reference_pu']-before[0]['g.TGOV1.reference_pu']==pytest.approx(.6)


@pytest.mark.parametrize('mode',['passport','equilibrium'])
def test_coincident_command_list_order_does_not_change_native_result(mode):
    traces=[run_study(StudySpec.model_validate(example(mode=mode,reverse=reverse))) for reverse in [False,True]]
    assert all(r['completed'] for r in traces)
    for key in ['time_s','sample_side','event_log','channels']:assert traces[0][key]==traces[1][key]


def test_event_gate_detects_wrong_controller_and_bound():
    s=example();r=run_study(StudySpec.model_validate(s));ref=LimiterReference(s).run()
    for key,value in [('controller','WRONG'),('bound','lower')]:
        broken=copy.deepcopy(r);event=next(e for e in broken['event_log'] if e['kind']=='controller_limit_enter');event[key]=value
        assert not compare(ref,broken)['passed']


def test_independent_hybrid_solver_never_calls_native_limiter_or_controller(monkeypatch):
    from power_grid_env.controller_boundaries import ControllerBoundaries
    from power_grid_env.coupled_controls import CoupledControllerBank
    def forbidden(*a,**k):raise AssertionError('native helper used by independent hybrid reference')
    for key in ['guards','apply','refresh','derivative','rates']:monkeypatch.setattr(ControllerBoundaries,key,forbidden)
    for key in ['derivatives','initialize']:monkeypatch.setattr(CoupledControllerBank,key,forbidden)
    r=LimiterReference(example()).run();coverage(r.events,'both')


def test_reference_rejects_unmodeled_protection():
    s=example();s['coupled']['network_automation']['relays'][0]['pickup']=1000.
    with pytest.raises(ValueError,match='inactive-protection'):LimiterReference(s)
