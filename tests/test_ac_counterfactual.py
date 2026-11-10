from dataclasses import replace
import copy
import pytest
from tests.test_ac_sensitivity import document as sensitivity_document
from tests.test_ac_dispatch_arc import study
from power_grid_env.ac_counterfactual import run_counterfactual,verify_counterfactual
from power_grid_env.ac_dispatch import solve_dispatch
from power_grid_env.ac_pseudo_arc import trace_power_flow
from power_grid_env.balanced_ac import case_from_document
from power_grid_env.study_artifacts import encoded,read_json,seal,export_zip,extract_zip


def document(amount=.01):
    s=sensitivity_document()
    return dict(case=s['case'],changes=[dict(bus_id=1,dp_mw=1.)],amount=amount,outputs=s['outputs'],options=dict(enforce_q_limits=True,dispatch=s['dispatch'],step=.1,maximum_step=.1))


def switching(reverse=False):
    s=study(reverse);a=case_from_document(s['start']);b=case_from_document(s['target'])
    return dict(case=s['start'],changes=[dict(bus_id=x.id,dp_mw=y.pd-x.pd,dq_mvar=y.qd-x.qd) for x,y in zip(a.buses,b.buses)],outputs=[dict(name='pg0',kind='pg',generator_index=0),dict(name='voltage',kind='voltage',bus_id=0)],options=s['options'],monitor=s['monitor'])


def test_finite_endpoint_matches_closed_form_and_prediction_error_is_second_order(tmp_path):
    errors=[]
    for i,h in enumerate([.01,.005]):
        r=run_counterfactual(document(h),tmp_path/str(i));assert r['completed'] and r['requested_change_reached']
        v=next(o for o in r['outputs'] if o['name']=='v');p=.3+h
        expected=((1+(1-4*p*p)**.5)/2)**.5
        assert v['actual_after']==pytest.approx(expected,abs=1e-9);errors.append(abs(v['linear_error']))
        assert r['linear_applicability']=='no_control_switch_observed';assert verify_counterfactual(tmp_path/str(i))['verified']
    assert .2<errors[1]/errors[0]<.3


@pytest.mark.parametrize('reverse',[False,True])
def test_p_limit_switch_does_not_use_extrapolated_linear_endpoint(tmp_path,reverse):
    r=run_counterfactual(switching(reverse),tmp_path/'scan');assert r['completed'] and r['active_set_changed']
    assert r['linear_applicability']=='observed_control_switch'
    p=next(o for o in r['outputs'] if o['name']=='pg0');assert abs(p['linear_error'])>.001
    assert any(e['kind']=='p' and e['within_requested_interval'] for e in r['events'])
    assert verify_counterfactual(tmp_path/'scan')['verified']


def test_zero_and_negative_amounts_are_exact_finite_scenarios(tmp_path):
    for i,amount in enumerate([0.,-.01]):
        r=run_counterfactual(document(amount),tmp_path/str(i));assert r['completed']
        p=next(o for o in r['outputs'] if o['name']=='p')
        assert p['actual_change']==pytest.approx(amount,abs=1e-9)


def test_fold_is_not_reported_as_a_solved_endpoint(tmp_path):
    r=run_counterfactual(document(.4),tmp_path/'fold')
    assert not r['completed'] and not r['requested_change_reached'] and r['reason']=='sampled_turn_before_target'
    assert all(o['actual_after'] is None for o in r['outputs']) and not r['global_target_absence_proven']
    assert verify_counterfactual(tmp_path/'fold')['verified']


def test_zero_current_can_be_simulated_without_a_fake_linear_derivative(tmp_path):
    d=document(.1);d['case']['buses'][1]['pd']=0.;d['outputs']=[dict(name='i',kind='branch_current',branch_index=0,terminal='from')]
    r=run_counterfactual(d,tmp_path/'zero');assert r['completed'] and not r['linear_prediction_available']
    assert r['linear_reason']=='zero_magnitude_not_differentiable'
    assert r['outputs'][0]['before']==0 and r['outputs'][0]['actual_after']>0 and r['outputs'][0]['linear_after'] is None
    assert verify_counterfactual(tmp_path/'zero')['verified']


def test_audited_seed_selects_branch_without_cold_dispatch(monkeypatch,tmp_path):
    import power_grid_env.ac_pseudo_arc as native
    d=document();c=case_from_document(d['case']);seed=solve_dispatch(c,d['options']['dispatch'])['result'];d['state']=seed
    def forbidden(*a,**kw):raise AssertionError('cold dispatch called despite audited seed')
    monkeypatch.setattr(native,'solve_dispatch',forbidden)
    r=run_counterfactual(d,tmp_path/'warm');assert r['completed'];assert verify_counterfactual(tmp_path/'warm')['verified']
    s=read_json(tmp_path/'warm/study/trace.json');assert s['points'][0]['result']['vm_pu']==seed['vm_pu']


@pytest.mark.parametrize('kind',['supplied','auto'])
def test_cancelled_scenario_has_no_counterfeit_endpoint(tmp_path,kind):
    d=document()
    if kind=='supplied':d['state']=solve_dispatch(case_from_document(d['case']),d['options']['dispatch'])['result']
    r=run_counterfactual(d,tmp_path/'cancelled',cancelled=lambda:True)
    assert not r['completed'] and not r['requested_change_reached'];assert verify_counterfactual(tmp_path/'cancelled')['verified']


@pytest.mark.parametrize('kind',['actual','linear','claim','seed'])
def test_semantic_tampering_after_resealing_rejected(tmp_path,kind):
    d=document();d['state']=solve_dispatch(case_from_document(d['case']),d['options']['dispatch'])['result'];p=tmp_path/'scan';run_counterfactual(d,p)
    if kind=='seed':
        path=p/'study/input.json';r=read_json(path);r['initial_state']['vm_pu'][1]*=.9
    else:
        path=p/'result.json';r=read_json(path)
        if kind=='actual':r['outputs'][0]['actual_after']+=.1
        elif kind=='linear':r['outputs'][0]['linear_after']+=.1
        else:r['global_target_absence_proven']=True
    path.write_bytes(encoded(r))
    if kind=='seed':(p/'study/manifest.json').unlink();seal(p/'study')
    (p/'manifest.json').unlink();seal(p)
    with pytest.raises(ValueError):verify_counterfactual(p)


def test_cli_and_zip(tmp_path,capsys):
    from power_grid_env.cli import main
    path=tmp_path/'input.json';path.write_bytes(encoded(document()))
    assert main(['ac-counterfactual','--input',str(path),'--out',str(tmp_path/'scan')])==0
    assert main(['verify-ac-counterfactual',str(tmp_path/'scan')])==0
    export_zip(tmp_path/'scan',tmp_path/'scan.zip');extract_zip(tmp_path/'scan.zip',tmp_path/'restored');assert verify_counterfactual(tmp_path/'restored')['verified']


@pytest.mark.parametrize('bad',[dict(changes=[dict(bus_id=999,dp_mw=1.)]),dict(changes=[dict(bus_id=1)]*2),dict(amount=float('nan')),dict(options={})])
def test_invalid_input_rejected_before_publication(tmp_path,bad):
    d=document();d.update(bad)
    with pytest.raises(ValueError):run_counterfactual(d,tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()


def test_initial_boundary_refuses_linear_prediction_but_allows_finite_replay(tmp_path):
    from tests.test_ac_dispatch import network,opts
    from power_grid_env.balanced_ac import case_document
    c=network();c=replace(c,generators=[replace(c.generators[0],pmax=.25),*c.generators[1:]])
    d=dict(case=case_document(c),changes=[dict(bus_id=1,dp_mw=.01)],outputs=[dict(name='p0',kind='pg',generator_index=0)],options=dict(enforce_q_limits=True,dispatch=opts(),step=.1,maximum_step=.1))
    r=run_counterfactual(d,tmp_path/'boundary');assert r['completed'] and not r['linear_prediction_available']
    assert r['linear_reason']=='active_set_boundary' and r['outputs'][0]['linear_after'] is None
    assert r['outputs'][0]['actual_after']==pytest.approx(.25,abs=1e-9);assert verify_counterfactual(tmp_path/'boundary')['verified']


def test_partial_monitor_cannot_be_presented_as_complete_study(tmp_path):
    d=document();d['monitor']=dict(maximum_wall_s=1e-12)
    r=run_counterfactual(d,tmp_path/'partial');assert r['requested_change_reached'] and not r['completed']
    assert verify_counterfactual(tmp_path/'partial')['verified']


def test_bad_seed_rejected_before_envelope_directory_is_published(tmp_path):
    from power_grid_env.ac_envelope import run_envelope
    d=document();c=case_from_document(d['case']);seed=solve_dispatch(c,d['options']['dispatch'])['result'];seed['vm_pu'][1]*=.9
    native=dict(start=d['case'],target=d['case'],options=d['options'],initial_state=seed)
    with pytest.raises(ValueError):run_envelope(native,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_base_origin_cannot_be_forged_after_reseal(tmp_path):
    out=tmp_path/'scenario';run_counterfactual(document(),out);base=read_json(out/'base.json');base['reason']='independently_certified'
    (out/'base.json').write_bytes(encoded(base));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError,match='origin'):verify_counterfactual(out)


def test_benchmark_reference_rendering_tamper_is_rejected(tmp_path,monkeypatch):
    import benchmarks.ac_counterfactual_acceptance as bench
    monkeypatch.setattr(bench,'SPECS',('p-entry',));monkeypatch.setattr(bench,'sources',lambda:{'test':'fixed'})
    out=tmp_path/'assessment';assert bench.qualify(out)['passed'];assert bench.verify_qualification(out)['verified']
    refs=read_json(out/'p-entry-references.json');refs[0]['reference']['result']['branch_power_mva'][0][0]+=.01
    (out/'p-entry-references.json').write_bytes(encoded(refs));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError):bench.verify_qualification(out)
