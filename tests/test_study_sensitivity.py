import math
import numpy as np
import pytest
from power_grid_env.study_sensitivity import differentiate_study,SensitivityRequest
from power_grid_env.hybrid_resets import ResetRow,apply_resets,reset_product,event_reset_rows
from benchmarks.network_automation_reference import example


def request(path,step,outputs):
    return SensitivityRequest(parameters=[dict(path=path,step=step,unit='SI')],outputs=outputs,isolate=False,derivative_atol=1e-6)


def test_ats_delay_derivative_includes_hidden_deadlines_and_topology():
    s=example('standby_transfer');s.coupled.rtol=1e-11;s.coupled.atol=1e-13
    # Locate the event index once; driver then verifies the entire order.
    from power_grid_env.study_spec import run_study
    base=run_study(s);index=next(i for i,e in enumerate(base['event_log']) if e['kind']=='breaker_close' and e['equipment_id']=='reserve-cb')
    r=differentiate_study(s,request('/coupled/network_automation/breakers/2/closing_delay_s',.001,['/event_times/'+str(index),'/final/g.delta']))
    assert r['available'],r
    assert r['parameters'][0]['derivative'][0]==pytest.approx(1.,abs=1e-7)
    assert len(r['runs'])==7 and r['includes_consistent_initialization']


def test_initial_pv_constraint_derivative_matches_independent_implicit_si():
    from scipy.optimize import brentq
    s=example('matched_island');s.coupled.events=[];s.coupled.duration_s=.02;s.network.generators[0].p_w=200000.;s.coupled.rtol=1e-11;s.coupled.atol=1e-13
    r=differentiate_study(s,request('/network/sources/0/r_ohm',.001,['/initial/g.delta','/operating_point/generator_initial/g/internal_voltage_pu']))
    assert r['available'],r
    vb=10000/math.sqrt(3);z=2.2+9j;zg=25j;yl=(300000-100000j)/10000**2
    def quantities(theta):
        v=vb*np.exp(1j*theta);i=yl*v+(v-vb)/z
        return v,i
    theta=brentq(lambda a:3*(quantities(a)[0]*np.conj(quantities(a)[1])).real-200000,-.2,.2,xtol=1e-14)
    v,i=quantities(theta);it=1j*v*(yl+1/z);ir=-(v-vb)/z**2
    dt=-3*(v*np.conj(ir)).real/(3*(1j*v*np.conj(i)+v*np.conj(it)).real)
    e=v+zg*i;de=1j*v*dt+zg*(it*dt+ir)
    expected=[(de/e).imag,(np.conj(e)*de).real/abs(e)/vb]
    np.testing.assert_allclose(r['parameters'][0]['derivative'],expected,rtol=2e-4,atol=2e-7)


def test_noncommuting_reset_chain_and_parameter_pushforward():
    x=np.array([2.,3.]);v=np.array([.4,.7]);ops=[ResetRow(0,1.,{1:2.}),ResetRow(1,0.,{0:3.},{'bound':1.})]
    derivative=reset_product(v,ops,{'bound':.2});h=1e-6
    plus=[ops[0],ResetRow(1,h*.2,{0:3.})];minus=[ops[0],ResetRow(1,-h*.2,{0:3.})]
    fd=(apply_resets(x+h*v,plus)-apply_resets(x-h*v,minus))/(2*h)
    np.testing.assert_allclose(derivative,fd,atol=1e-8)
    assert not np.allclose(apply_resets(x,ops),apply_resets(x,list(reversed(ops))))


def test_actual_controller_reset_map_matches_primary_action():
    from benchmarks.genrou_jacobian_validation import case
    from tests.test_esst1a_jacobian import ready
    s=case('genrou_limits_recovery');m,x=ready(s);a=next(a for a,entry in m.controller_boundaries.entries.items() if entry[1]=='SEXS')
    x[a]+=10;before=x.copy();log=[];m.controller_boundaries.apply({('controller',a,'upper')},.1,x,log)
    ops=event_reset_rows(m,log[0]);np.testing.assert_allclose(apply_resets(before,ops),x)
    assert reset_product(np.ones(len(x)),ops)[a]==0


@pytest.mark.parametrize('path',['/coupled/step_s','/network/frequency_hz~3','/network/sources/-1/r_ohm'])
def test_invalid_parameter_or_policy_rejected(path):
    s=example('matched_island')
    with pytest.raises(ValueError):differentiate_study(s,request(path,.001,['/final/g.delta']))


def test_noncommuting_scheduled_cascade_is_validated_and_order_change_rejected():
    from benchmarks.hybrid_trajectory_validation import case
    from power_grid_env.study_spec import StudySpec,run_study
    s=case(False);s=StudySpec.model_validate_json(s.model_dump_json())
    baseline=run_study(s);assert baseline['completed'],baseline
    assert [e['kind'] for e in baseline['event_log'][:2]]==['motor_close','motor_open']
    r=differentiate_study(s,request('/induction/events/1/time_s',1e-4,['/final/m.speed_pu']))
    assert not r['available'] and 'event_sequence_changed' in r['reason']


def test_sampled_internal_trace_preserves_solver_and_physical_grid():
    from benchmarks.genrou_jacobian_validation import case
    from power_grid_env.study_spec import run_study
    s=case('genrou_limits_recovery');s.coupled.event_policy='coincident_roots_v1'
    a=run_study(s);s.coupled.event_trace='sampled_controls';b=run_study(s)
    assert a['completed'] and b['completed']
    assert a['event_log']==b['event_log']
    for j,t in enumerate(b['time_s']):
        indices=[i for i,at in enumerate(a['time_s']) if at==t and a['sample_side'][i]==b['sample_side'][j]]
        assert indices
        for key in a['channels']:assert a['channels'][key][indices[-1]]==b['channels'][key][j]


@pytest.mark.parametrize('family',['uel','esst1a'])
def test_actual_lag_reset_maps_match_primary_assignments(family):
    from tests.test_esst1a_jacobian import ready
    if family=='uel':
        from benchmarks.generator_underexcitation_cases import example
        m,x=ready(example('hold'));bank=m.underexcitation_limiters;i=next(iter(bank.entries));a,p=bank.entries[i]
        bank.regions[i]=1;x[a]=p.MEL_MAX+.01;before=x.copy();log=[];bank.apply({('uel',i,'reset')},.1,x,log)
        event=next(e for e in log if e['kind']=='uel_lag_reset')
    else:
        from benchmarks.generator_esst1a_cases import example
        m,x=ready(example('hold'));bank=m.esst1a;i=next(iter(bank.entries));a,p=bank.entries[i]
        bank.regions[i]=1;x[a+3]=p.V_AMAX+.01;before=x.copy();log=[];bank.apply({('esst1a',i,'reset')},.1,x,log)
        event=next(e for e in log if e['kind']=='esst1a_regulator_reset')
    ops=event_reset_rows(m,event);np.testing.assert_allclose(apply_resets(before,ops),x)
    assert reset_product(np.ones(len(x)),ops)[ops[0].row]==0


def test_central_zero_at_a_corner_is_not_reported_as_a_gradient(monkeypatch):
    s=example('matched_island')
    from power_grid_env.evidence import package_fingerprint
    def fake(spec,**kwargs):
        y=abs(spec.network.sources[0].r_ohm-2.)
        return dict(completed=True,status='completed',code_sha256=package_fingerprint(),channels={'y':[y,y]},event_log=[])
    monkeypatch.setattr('power_grid_env.study_sensitivity.run_study',fake)
    r=differentiate_study(s,request('/network/sources/0/r_ohm',.01,['/final/y']))
    assert not r['available'] and r['parameters'][0]['reason']=='one_sided_corner_or_grazing'
    assert r['parameters'][0]['derivative'] is None


def test_reset_parameter_directions_are_real():
    with pytest.raises(ValueError,match='real'):reset_product(np.ones(2),[ResetRow(0,0.,parameter_weights={'p':1.})],{'p':1j})
