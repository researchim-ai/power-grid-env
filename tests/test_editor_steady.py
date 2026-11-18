"""Native OpenDSS bridge tests against SI series references and switch islands."""
import cmath
import math
import pytest
from benchmarks.grid_editor import compile_draft,solve
from tests.test_grid_editor import draft,multivoltage_draft


def passive(fault=False):
    d=draft();d.update(profile='passive_3ph_fault' if fault else 'passive_power_flow',motors=[],loads=[] if fault else [dict(id='z1',node_id='n2',p_kw=20.,q_kvar=5.)],fault=dict(node_id='n2',r_ohm=.001,x_ohm=0.))
    return d


def test_pf_matches_independent_si_series_impedance():
    d=passive();r=solve(d);v=next(n for n in r['nodes'] if n['id']=='n2')['phases']['A']
    z=complex(.006+.4*.09,.024+.08*.09);y=complex(20000,-5000)/400**2
    expected=(400/math.sqrt(3))/(1+z*y)
    assert v['voltage_v']==pytest.approx(abs(expected),rel=2e-8)
    assert v['angle_deg']==pytest.approx(math.degrees(cmath.phase(expected)),abs=2e-6)
    assert r['source']['p_kw']==pytest.approx(sum(l['actual']['p_kw'] for l in r['loads'])+sum(b['loss_kw'] for b in r['branches']),abs=1e-7)


def test_fault_matches_independent_source_series_reference():
    d=passive(True);r=solve(d);z=complex(.006+.4*.09+.001,.024+.08*.09)
    expected=(400/math.sqrt(3))/abs(z)
    for value in r['fault']['phase_current_a'].values():assert value==pytest.approx(expected,rel=2e-8)
    assert r['fault']['current_a']>4000


def test_native_pf_mesh_not_silently_reduced_to_a_tree():
    d=passive();d['lines'].append(dict(id='parallel',from_node='n0',to_node='n2',length_m=30.,r_ohm_km=.4,x_ohm_km=.08,rating_a=180.))
    r=solve(d);z1=complex(.4*.09,.08*.09);z2=complex(.4*.03,.08*.03);z=complex(.006,.024)+z1*z2/(z1+z2);y=complex(20000,-5000)/400**2
    expected=(400/math.sqrt(3))/(1+z*y)
    assert len(r['branches'])==3
    assert next(n for n in r['nodes'] if n['id']=='n2')['phases']['A']['voltage_v']==pytest.approx(abs(expected),rel=2e-8)


def switched(closed=True,fault=False):
    d=passive(fault);d['lines']=d['lines'][:1];d['switches']=[dict(id='q1',from_node='n1',to_node='n2',closed=closed,interrupt_current_a=100.)]
    return d


def test_open_switch_marks_island_and_has_no_fake_measured_current():
    live=solve(switched());dead=solve(switched(False));assert all(n['energized'] for n in live['nodes'])
    node=next(n for n in dead['nodes'] if n['id']=='n2');assert not node['energized'];assert all(p['voltage_v']==0 for p in node['phases'].values())
    sw=next(b for b in dead['branches'] if b['id']=='q1');assert sw['from_terminal'] is None and sw['loading_pct'] is None
    assert dead['loads'][0]['actual'] is None
    assert len(dead['islands'])==2 and dead['converged']


def test_fault_on_dead_island_is_explicitly_rejected():
    with pytest.raises(ValueError,match='deenergized'):solve(switched(False,True))


def test_breaker_fault_duty_is_screened_without_automatic_trip():
    r=solve(switched(True,True));sw=next(b for b in r['branches'] if b['id']=='q1')
    assert sw['closed'] and sw['passport']['closed']
    assert sw['interrupting_screen']['exceeds_rating'] is True
    assert sw['loss_kw']==pytest.approx(3*sw['from_terminal']['current_a']**2*.0001/1000,rel=2e-8)


def test_native_multivoltage_transformer_pf_preserves_local_bases():
    d=multivoltage_draft();d.update(profile='passive_power_flow',motors=[],loads=[dict(id='z1',node_id='n2',p_kw=20.,q_kvar=5.)])
    r=solve(d);assert r['backend']['name'];assert r['source']['p_kw']>0
    tr=next(b for b in r['branches'] if b['id']=='tr1');assert tr['to_terminal']['current_a']/tr['from_terminal']['current_a']==pytest.approx(25,rel=1e-3)
    assert next(n for n in r['nodes'] if n['id']=='hv')['nominal_ll_v']==10000


def test_native_document_exports_fault_and_switches_explicitly():
    d=switched(True,True);_,s,_=compile_draft(d)
    assert s.study=='power_flow' and s.backend=='opendss' and s.induction is None
    assert not s.network.switches
    assert next(l for l in s.network.lines if l.id=='q1').r_ohm[0][0]==.0001
    assert any('Editor static switch snapshot' in note for note in s.network.assumptions)
    assert s.network.study_faults[0].bus_id=='n2'


@pytest.mark.parametrize('mutate',[
 lambda d:d.update(motors=draft()['motors']),
 lambda d:d.update(events=[dict(time_s=.1,kind='source_voltage',equipment_id='source',value=.8)]),
 lambda d:d['fault'].update(r_ohm=0.),
 lambda d:d['fault'].update(node_id='missing'),
 lambda d:d.update(profile='lv_motor_start'),
 lambda d:d['switches'][0].update(r_ohm=-.1),
])
def test_unsupported_static_cases_fail_before_solver(mutate):
    d=switched(True,True);mutate(d)
    with pytest.raises(ValueError):compile_draft(d)


def test_exported_native_document_reproduces_physical_switch_flow():
    from power_grid_env.study_spec import run_study_document
    d=switched(True,True);_,spec,_=compile_draft(d);r=run_study_document(spec.model_dump(mode='json'))
    assert r['completed'] and r['result']['converged']
    currents=[math.hypot(f['i_into_real_a'],f['i_into_imag_a']) for f in r['result']['flows'] if f['equipment_id']=='q1' and f['terminal_index']==0]
    expected=solve(d)['branches'][-1]['from_terminal']['current_a']
    assert max(currents)==pytest.approx(expected,rel=1e-10)


def test_disconnector_does_not_receive_breaker_interrupting_approval():
    d=switched(True,True);d['switches'][0]['kind']='disconnector'
    r=solve(d);screen=next(b for b in r['branches'] if b['id']=='q1')['interrupting_screen']
    assert screen['exceeds_rating'] is None
