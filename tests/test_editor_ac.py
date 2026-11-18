import math
import pytest
from benchmarks.grid_editor import solve,compile_draft
from tests.test_editor_steady import passive,switched


def ac(model='constant_pq'):
    d=passive();d['profile']='ac_power_flow';d['loads'][0]['model']=model;return d


@pytest.mark.parametrize('model,law',[
 ('constant_pq',lambda v:1),('constant_i',lambda v:v),('constant_z',lambda v:v*v),('zip',lambda v:.3*v*v+.3*v+.4)
])
def test_native_load_laws_against_independent_voltage_formula(model,law):
    r=solve(ac(model));l=r['loads'][0];v=next(n for n in r['nodes'] if n['id']=='n2')['phases']['A']['voltage_pu']
    assert l['actual']['p_kw']==pytest.approx(20*law(v),rel=2e-7)
    assert l['actual']['q_kvar']==pytest.approx(5*law(v),rel=2e-7)


def test_pq_generation_reverse_power_and_system_balance():
    d=ac();d['generators']=[dict(id='g1',node_id='n2',p_kw=30.,q_kvar=2.,rated_kva=100.)];r=solve(d);g=r['generators'][0]
    assert g['actual']['p_kw']==pytest.approx(30,abs=1e-6)
    assert r['source']['p_kw']<0
    assert r['source']['p_kw']+g['actual']['p_kw']==pytest.approx(r['loads'][0]['actual']['p_kw']+sum(b['loss_kw'] for b in r['branches']),abs=1e-6)


@pytest.mark.parametrize('target,cap,limited',[(1.,100.,False),(1.05,2.,True)])
def test_pv_voltage_control_and_q_limit_are_distinct(target,cap,limited):
    d=ac();d['generators']=[dict(id='g1',node_id='n2',mode='pv',p_kw=30.,rated_kva=200.,v_set_pu=target,q_min_kvar=-cap,q_max_kvar=cap)];g=solve(d)['generators'][0]
    assert g['q_limited'] is limited
    if limited:
        assert g['actual']['q_kvar']==pytest.approx(cap,abs=1e-5)
        assert abs(g['voltage_pu']-target)>.01
    else:assert g['voltage_pu']==pytest.approx(target,abs=1e-6)


def test_islanded_pq_generator_does_not_invent_voltage():
    d=switched(False);d['profile']='ac_power_flow';d['generators']=[dict(id='g1',node_id='n2',p_kw=30.)];r=solve(d)
    assert not r['generators'][0]['energized'] and r['generators'][0]['actual'] is None


def test_load_outside_declared_window_rejects_native_fallback():
    d=ac();d['loads'][0]['vmin_pu']=.9999
    with pytest.raises(ValueError,match='fallback'):solve(d)


@pytest.mark.parametrize('change',[
 lambda d:d['loads'][0].update(model='zip',zip_z=.9),
 lambda d:d.update(profile='passive_3ph_fault'),
 lambda d:d.update(generators=[dict(id='g1',node_id='n2',rated_kva=10.,p_kw=30.)]),
 lambda d:d.update(generators=[dict(id='g1',node_id='n2',mode='pv',q_min_kvar=40.,q_max_kvar=-40.)]),
 lambda d:d.update(generators=[dict(id='g1',node_id='missing')]),
])
def test_incompatible_law_or_generator_is_rejected(change):
    d=ac();change(d)
    with pytest.raises(ValueError):compile_draft(d)


def test_zip_active_and_reactive_weights_are_independent():
    d=ac('zip');d['loads'][0].update(zip_z=.2,zip_i=.3,zip_p=.5,zip_zq=.6,zip_iq=.1,zip_pq=.3)
    r=solve(d);v=next(n for n in r['nodes'] if n['id']=='n2')['phases']['A']['voltage_pu'];l=r['loads'][0]
    assert l['actual']['p_kw']==pytest.approx(20*(.2*v*v+.3*v+.5),rel=2e-7)
    assert l['actual']['q_kvar']==pytest.approx(5*(.6*v*v+.1*v+.3),rel=2e-7)
    assert abs(l['law_check']['p_residual_kw'])<1e-5
