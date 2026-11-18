import copy
import numpy as np
import pytest

from benchmarks.topology_stator_cases import example
from benchmarks.topology_stator_reference import TopologyStatorReference
from benchmarks.topology_stator_acceptance import compare,coverage
from power_grid_env.study_spec import StudySpec,run_study


@pytest.fixture(scope='module')
def refs():
    return {kind:TopologyStatorReference(example(kind)).run() for kind in
            ('source_cycle','line_cycle','dual_cycle','source_blocked')}


@pytest.fixture(scope='module')
def reports():
    rows={}
    for method in ('radau','network_dae'):
        for kind in ('source_cycle','line_cycle','dual_cycle','source_blocked'):
            s=example(kind);s['coupled']['integration_method']=method
            rows[method,kind]=run_study(StudySpec.model_validate(s))
    return rows


@pytest.mark.parametrize('method',['radau','network_dae'])
@pytest.mark.parametrize('kind',['source_cycle','line_cycle','dual_cycle','source_blocked'])
def test_island_and_reconnection_matches_independent_SI(method,kind,refs,reports):
    r=reports[method,kind]
    assert r['completed'] and r['additional_validation']['ok']
    assert compare(refs[kind],r)['passed']
    assert coverage(r['event_log'],kind)==coverage(refs[kind].events,kind)


@pytest.mark.parametrize('time',[.02,.12,.16,.20])
def test_SI_voltage_rate_including_empty_node_matches_directional_difference(time,refs):
    r=refs['dual_cycle'];x,c=r.state_at(time);f=r.rhs(time,x,c);h=1e-6
    expected=(np.array(r.circuit(x+h*f,c)[2:])-np.array(r.circuit(x-h*f,c)[2:]))/(2*h)
    np.testing.assert_allclose(r.voltage_rates(time,x,c),expected,rtol=2e-7,atol=2e-6)
    if time==.12:
        assert r.circuit(x,c)[3]==0 and r.voltage_rates(time,x,c)[1]==0
        assert abs(r.circuit(x,c)[2])>r.gv*.5


@pytest.mark.parametrize('mutation',['frequency','reason','allowed','dead_frequency','empty_component','attempt'])
def test_wrong_synchronism_or_topology_evidence_fails(mutation,refs,reports):
    report=copy.deepcopy(reports['radau','dual_cycle'])
    checks=[e['synchronism'] for e in report['event_log'] if 'synchronism' in e]
    if mutation=='frequency':checks[-1]['right_frequency_hz']+=.01
    elif mutation=='reason':checks[-1]['reason']='dead_receiver'
    elif mutation=='allowed':checks[-1]['allowed']=False
    elif mutation=='dead_frequency':checks[0]['right_frequency_hz']=50.
    elif mutation=='empty_component':report['channels']['network.zero_reference_components']=[0.]*len(report['time_s'])
    else:report['channels']['primary-cb.close_attempts'][-1]+=1
    assert not compare(refs['dual_cycle'],report)['passed']


def test_reference_does_not_call_native_topology_tangent_or_synchro_helpers(monkeypatch):
    import power_grid_env.network_automation as auto
    import power_grid_env.generator_stator as stator
    from power_grid_env.controller_boundaries import ControllerBoundaries
    def forbidden(*args,**kwargs):raise AssertionError('native helper called')
    for k in ('voltage_derivatives','synchronism','generator_emf_derivative'):
        monkeypatch.setattr(auto,k,forbidden)
    for k in ('guards','apply','service','refresh','check_close','set_position'):
        monkeypatch.setattr(auto.NetworkAutomationBank,k,forbidden)
    for k in ('guards','apply','refresh','rates'):
        monkeypatch.setattr(ControllerBoundaries,k,forbidden)
    monkeypatch.setattr(stator,'impedance',forbidden)
    ref=TopologyStatorReference(example('dual_cycle')).run()
    assert coverage(ref.events,'dual_cycle')['dead_receiver_closings']==1


@pytest.mark.parametrize('kind',['source_cycle','line_cycle','dual_cycle','source_blocked'])
def test_continuous_states_survive_switching(kind,refs):
    r=refs[kind]
    for t,pre,pc,post,c in r.boundaries:
        np.testing.assert_allclose(pre[:4],post[:4],atol=1e-12,rtol=0)
        np.testing.assert_allclose(pre[-6:],post[-6:],atol=1e-12,rtol=0)
        assert pre[r.heat_address]==post[r.heat_address]
    old=r.sample(.12);r.sample(.5);assert old==r.sample(.12)


@pytest.mark.parametrize('feature',['reclose','active_relay','failure','source_voltage'])
def test_unsupported_combined_profiles_rejected(feature):
    s=example();a=s['coupled']['network_automation']
    if feature=='reclose':a['breakers'][0]['maximum_reclose_shots']=1
    elif feature=='active_relay':a['relays'][0]['pickup']=1.
    elif feature=='failure':a['breakers'][0]['fail_open']=True
    else:s['coupled']['events'].append(dict(time_s=.12,kind='source_voltage',equipment_id='source',value=0.))
    with pytest.raises(ValueError):TopologyStatorReference(s)


@pytest.mark.parametrize('method',['radau','network_dae'])
def test_relay_breaker_and_command_permutation(method,reports):
    s=example('dual_cycle',reverse=True);s['coupled']['integration_method']=method
    actual=run_study(StudySpec.model_validate(s));expected=reports[method,'dual_cycle']
    assert actual['completed']
    for k in ('event_log','time_s','sample_side','channels'):assert actual[k]==expected[k]
