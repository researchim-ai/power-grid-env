from dataclasses import replace
import copy
import numpy as np
import pytest
from power_grid_env.balanced_ac import case_document
from power_grid_env.ac_pseudo_arc import trace_power_flow,audit_trace,make_family,PseudoArcOptions,run_pseudo_arc,verify_pseudo_arc
from power_grid_env.ac_dispatch import solve_dispatch
from power_grid_env.ac_envelope import run_envelope,verify_envelope
from power_grid_env.study_artifacts import encoded,read_json,seal,export_zip,extract_zip
from tests.test_ac_dispatch import network,opts


def study(reverse=False,step=.04,base=1.):
    c=network();c=replace(c,base_mva=base,buses=[replace(b,pd=b.pd*base,qd=b.qd*base) for b in c.buses],
        generators=[replace(g,pg=g.pg*base,pmin=g.pmin*base,pmax=(.24 if i==0 else g.pmax)*base,qmin=g.qmin*base,qmax=g.qmax*base) for i,g in enumerate(c.generators)],
        branches=[replace(b,rate_a=.2*base) for b in c.branches])
    a=replace(c,buses=[replace(b,pd=b.pd*.7) for b in c.buses]);b=replace(c,buses=[replace(b,pd=b.pd*1.3) for b in c.buses])
    if reverse:a,b=b,a
    return dict(start=case_document(a),target=case_document(b),options=dict(enforce_q_limits=True,dispatch=opts(),step=step,maximum_step=step),monitor=dict(current_limits=[dict(branch_index=1,terminal='from',maximum_pu=.14)]))


def models(d):
    from power_grid_env.balanced_ac import case_from_document
    return case_from_document(d['start']),case_from_document(d['target'])


@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('step',[.04,.02])
def test_p_limit_entry_release_localized_at_exact_lossless_load(reverse,step):
    d=study(reverse,step);a,b=models(d);r=trace_power_flow(a,b,d['options'])
    assert r['completed'],r['reason']
    assert r['control_model']=='distributed-pq/v1' and r['slack_q_enforced']
    assert len(r['p_control_changes'])==1
    e=r['p_control_changes'][0];assert e['generator_index']==0
    assert (e['before'],e['after'])==(('upper','participating') if reverse else ('participating','upper'))
    bracket=e['refinement'];assert bracket['localized']
    # Total load = .4 + delta; Pg0=.2+.25*delta=.24 => total load .56.
    exact=(.78-.56)/.36 if reverse else (.56-.42)/.36
    lo,hi=sorted([bracket['left']['lambda_value'],bracket['right']['lambda_value']])
    assert lo-2e-8<=exact<=hi+2e-8
    assert audit_trace(d,r)['verified']
    exact_state=solve_dispatch(b,d['options']['dispatch'])
    assert r['final_result']['pg_mw']==pytest.approx(exact_state['result']['pg_mw'],abs=1e-9)
    assert r['final_result']['q_modes']['0']=='upper'


@pytest.mark.parametrize('base',[1.,100.])
def test_augmented_state_and_loading_column_match_finite_differences(base):
    d=study(base=base);a,b=models(d);o=PseudoArcOptions.model_validate(d['options']);f=make_family(a,b,o)
    for lam in [0.,.2,.8]:
        case=f.load_case(lam);r=solve_dispatch(case,d['options']['dispatch']);assert r['completed']
        u=f.pack(r['result'],lam);_,j=f.evaluate(u,True);expected=np.column_stack([j.toarray(),f.g.copy()])
        for i in range(len(u)):
            change=np.zeros(len(u));change[i]=1e-6
            assert (f.evaluate(u+change)-f.evaluate(u-change))/2e-6==pytest.approx(expected[:,i],abs=5e-8)


def test_dispatch_envelope_handles_native_p_q_controls_and_terminal_limits(tmp_path):
    d=study();r=run_envelope(d,tmp_path/'packet')
    assert r['trace']['completed'] and r['envelope']['completed']
    assert r['trace']['p_control_changes'] and r['trace']['final_result']['q_modes']['0']=='upper'
    assert r['envelope']['events'] and not r['envelope']['target_within_monitored_limits']
    assert verify_envelope(tmp_path/'packet')['verified']
    for point in r['trace']['points']:assert 'delta_mw' in point['result'] and 'angle_reference_bus' in point['result']
    export_zip(tmp_path/'packet',tmp_path/'packet.zip');extract_zip(tmp_path/'packet.zip',tmp_path/'restored')
    assert verify_envelope(tmp_path/'restored')==verify_envelope(tmp_path/'packet')


@pytest.mark.parametrize('kind',['delta','participation','p_mode','q_mode','p_inventory','p_history','p_endpoint','slack_claim'])
def test_semantic_control_tampering_rejected_after_resealing(tmp_path,kind):
    d=study();d.pop('monitor');p=tmp_path/'packet';run_pseudo_arc(d,p);r=read_json(p/'result.json')
    if kind=='delta':r['points'][1]['result']['delta_mw']+=.01
    elif kind=='participation':r['points'][1]['result']['participation'][0]=.5
    elif kind=='p_mode':r['points'][-1]['p_control_modes']['0']='participating'
    elif kind=='q_mode':r['points'][1]['result']['q_modes']['0']='voltage'
    elif kind=='p_inventory':r['p_control_changes']=[]
    elif kind=='p_history':r['points'][r['p_control_changes'][0]['after_point']]['p_control_brackets']['0']['history'][0]['arc_step']+=.001
    elif kind=='p_endpoint':r['points'][r['p_control_changes'][0]['after_point']]['p_control_brackets']['0']['right']['result']['delta_mw']+=.01
    else:r['slack_q_enforced']=False
    (p/'result.json').write_bytes(encoded(r));(p/'manifest.json').unlink();seal(p)
    with pytest.raises(ValueError):verify_pseudo_arc(p)


def test_base_seed_cancellation_and_unsupported_dispatch_options(monkeypatch):
    from power_grid_env import ac_pseudo_arc as m
    d=study();a,b=models(d)
    monkeypatch.setattr(m,'solve_dispatch',lambda *a,**kw:dict(completed=False,reason='cancelled'))
    r=trace_power_flow(a,b,d['options']);assert r['reason']=='cancelled' and not r['completed']
    assert audit_trace(d,r)['verified']
    for changes in [dict(enforce_q_limits=False),dict(tolerance_pu=1e-8)]:
        options={**d['options'],**changes}
        with pytest.raises(ValueError):PseudoArcOptions.model_validate(options)


def test_reanchoring_equivalence_and_attempt_budget(tmp_path):
    d=study();r=run_envelope(d,tmp_path/'original');d2=copy.deepcopy(d);d2['options']['dispatch']['angle_reference_bus']=2
    other=run_envelope(d2,tmp_path/'reanchored');assert other['trace']['completed']
    assert other['trace']['final_result']['vm_pu']==pytest.approx(r['trace']['final_result']['vm_pu'],abs=1e-9)
    assert other['trace']['final_result']['pg_mw']==pytest.approx(r['trace']['final_result']['pg_mw'],abs=1e-9)
    assert verify_envelope(tmp_path/'reanchored')['verified']
    d['options']['maximum_attempts']=1;r=run_envelope(d,tmp_path/'partial')
    assert not r['trace']['completed'] and verify_envelope(tmp_path/'partial')['verified']


def test_all_active_reserves_exhausted_never_means_target_reached():
    d=study();a,b=models(d);limits=[replace(g,pmax=g.pg+.02) for g in a.generators]
    a=replace(a,generators=limits);b=replace(b,generators=limits)
    r=trace_power_flow(a,b,d['options'])
    assert not r['completed'] and r['final_result'] is None and not r['target_absence_proven']
    assert audit_trace(dict(start=case_document(a),target=case_document(b),options=d['options']),r)['verified']


def test_fixed_p_q_ranges_are_not_fake_control_switches(tmp_path):
    d=study();a,b=models(d)
    generators=[replace(a.generators[0],qmin=0.,qmax=0.),replace(a.generators[1],pmin=.1,pmax=.1),a.generators[2]]
    a=replace(a,generators=generators);b=replace(b,generators=generators)
    d['start']=case_document(a);d['target']=case_document(b)
    r=run_envelope(d,tmp_path/'packet');assert r['trace']['completed'],r['trace']['reason']
    assert all(p['control_modes']['0']=='fixed' and p['p_control_modes']['1']=='fixed' for p in r['trace']['points'])
    assert verify_envelope(tmp_path/'packet')['verified']
    f=make_family(a,b,PseudoArcOptions.model_validate(d['options']));p=r['trace']['points'][0];u=f.pack(p['result'],0.)
    _,j=f.evaluate(u,True);expected=np.column_stack([j.toarray(),f.g.copy()])
    for i in range(len(u)):
        delta=np.zeros(len(u));delta[i]=1e-6
        assert (f.evaluate(u+delta)-f.evaluate(u-delta))/2e-6==pytest.approx(expected[:,i],abs=5e-8)


def test_pq_projection_zero_width_uses_constant_q_derivative():
    from tests.test_ac_reactive_arc import controlled
    from power_grid_env.ac_reactive_arc import ReactivePolarFamily
    a,b=controlled();a=replace(a,buses=[a.buses[0],replace(a.buses[1],pd=.1)],generators=[a.generators[0],replace(a.generators[1],qmin=0.,qmax=0.)]);b=a
    # V=1, theta=0 and Pgen=Pload gives exact Q=0 at the degenerate projection.
    f=ReactivePolarFamily(a,b);r=dict(vm_pu=[1.,1.],va_deg=[0.,0.]);u=f.pack(r,0.);_,j=f.evaluate(u,True)
    assert f.control(u)[2][1]=='fixed'
    for i in range(len(u)-1):
        delta=np.zeros(len(u));delta[i]=1e-6
        assert (f.evaluate(u+delta)-f.evaluate(u-delta))/2e-6==pytest.approx(j.toarray()[:,i],abs=5e-8)


def test_joint_timeline_orders_limit_before_p_saturation_and_rejects_tampering(tmp_path):
    from power_grid_env.ac_envelope import audit_envelope
    d=study();r=run_envelope(d,tmp_path/'packet');m=r['envelope']
    assert m['timeline_complete']
    groups=m['timeline_groups'];assert groups
    assert m['timeline_events'][groups[0]['event_indices'][0]]['kind']=='limit'
    assert m['timeline_events'][groups[1]['event_indices'][0]]['kind']=='p'
    bad=copy.deepcopy(m);bad['timeline_events'][0]['equipment']=999
    with pytest.raises(ValueError):audit_envelope(d,r['trace'],bad)


def test_exhaustion_stop_has_auditable_last_state():
    d=study();a,b=models(d);gens=[replace(g,pmax=g.pg+.02) for g in a.generators];a=replace(a,generators=gens);b=replace(b,generators=gens)
    r=trace_power_flow(a,b,d['options'])
    assert r['reason']=='participating_p_limits_active' and r['points']
    assert not r['completed'] and r['points'][-1]['lambda_value']==pytest.approx(1/9,abs=1e-8)
    assert audit_trace(dict(start=case_document(a),target=case_document(b),options=d['options']),r)['verified']


def test_cli_dispatch_controls_exposed_in_envelope_summary(tmp_path,capsys):
    from power_grid_env.cli import main
    path=tmp_path/'input.json';path.write_bytes(encoded(study()))
    assert main(['ac-envelope','--input',str(path),'--out',str(tmp_path/'packet')])==0
    import json
    public=json.loads(capsys.readouterr().out);assert public['p_control_changes']==1 and public['timeline_complete']
    assert main(['verify-ac-envelope',str(tmp_path/'packet')])==0
    assert json.loads(capsys.readouterr().out)['p_control_changes']==1


def test_cancel_during_last_control_event_cannot_publish_exact_target(monkeypatch):
    from power_grid_env import ac_pseudo_arc as module
    d=study();a,b=models(d);gens=[replace(a.generators[0],pmax=.2949),*a.generators[1:]];a=replace(a,generators=gens);b=replace(b,generators=gens)
    cancelled=[False];real=module.refine_control_change
    def stop_at_p_event(*args,**kw):
        result=real(*args,**kw)
        if kw.get('control_kind')=='p':cancelled[0]=True
        return result
    monkeypatch.setattr(module,'refine_control_change',stop_at_p_event)
    r=trace_power_flow(a,b,d['options'],cancelled=lambda:cancelled[0])
    assert r['reason']=='cancelled' and not r['completed'] and r['final_result'] is None
    assert r['points'][-1]['lambda_value']>=1.
    assert audit_trace(dict(start=case_document(a),target=case_document(b),options=d['options']),r)['verified']
