from dataclasses import replace
import copy,json,math
import numpy as np
import pytest
from power_grid_env.ac_envelope import EnvelopeOptions,terminal_values,measure,limits_for,run_envelope,verify_envelope,audit_envelope,monitor_trace
from power_grid_env.balanced_ac import case_document
from power_grid_env.study_artifacts import export_zip,extract_zip
from tests.test_ac_pseudo_arc import analytic


def study(reverse=False):
    a,b=analytic(.4);a=replace(a,buses=[a.buses[0],replace(a.buses[1],vmin=.95)],branches=[replace(a.branches[0],rate_a=.3)])
    b=replace(b,buses=[a.buses[0],replace(a.buses[1],pd=.4)],branches=a.branches)
    if reverse:a,b=b,a
    return dict(start=case_document(a),target=case_document(b),monitor=dict(current_limits=[dict(branch_index=0,terminal='from',maximum_pu=.25)]))


def test_closed_form_voltage_mva_current_crossings_and_distinct_target_status(tmp_path):
    d=study();r=run_envelope(d,tmp_path/'packet');m=r['envelope']
    assert r['trace']['completed'] and m['completed'] and m['target_within_monitored_limits'] is False
    assert not m['base_violations'] and not m['sampled_branch_within_monitored_limits']
    exact={'bus:1:voltage:lower':.95*math.sqrt(1-.95**2),
        'branch:0:mva:from':.3*math.sqrt(1-.3**2),'branch:0:mva:to':.3,
        'branch:0:current:from':.25*math.sqrt(1-.25**2)}
    assert {e['key'] for e in m['events']}==set(exact)
    for e in m['events']:
        assert e['localized'] and e['direction']=='exit'
        target=(exact[e['key']]-.1)/.3
        a,b=sorted([e['left']['lambda_value'],e['right']['lambda_value']])
        assert a-2e-8<=target<=b+2e-8
        assert b-a<2e-7
    assert verify_envelope(tmp_path/'packet')['verified']
    export_zip(tmp_path/'packet',tmp_path/'packet.zip');extract_zip(tmp_path/'packet.zip',tmp_path/'restored')
    assert verify_envelope(tmp_path/'restored')==verify_envelope(tmp_path/'packet')


def test_reverse_path_reports_reentry_and_initial_violations(tmp_path):
    r=run_envelope(study(True),tmp_path/'packet')
    assert r['envelope']['completed'] and r['envelope']['target_within_monitored_limits']
    assert len(r['envelope']['base_violations'])==4
    assert all(e['direction']=='entry' for e in r['envelope']['events'])
    assert verify_envelope(tmp_path/'packet')['verified']


@pytest.mark.parametrize('monitor,reason',[(dict(maximum_events=1),'monitor_event_budget'),(dict(maximum_refinement_solves=1),'monitor_attempt_budget'),(dict(refinement_iterations=1),'event_iteration_budget'),(dict(maximum_wall_s=1e-12),'monitor_wall_deadline')])
def test_monitor_budgets_keep_valid_partial_evidence(tmp_path,monitor,reason):
    d=study();d['monitor'].update(monitor);r=run_envelope(d,tmp_path/'packet')
    assert r['trace']['completed'] and not r['envelope']['completed'] and r['envelope']['reason']==reason
    v=verify_envelope(tmp_path/'packet');assert v['verified'] and not v['completed']


def test_cancel_monitor_without_discarding_audited_trace(tmp_path):
    d=study();r=run_envelope(d,tmp_path/'packet');m=monitor_trace(d,r['trace'],cancelled=lambda:True)
    assert not m['completed'] and m['reason']=='cancelled'
    assert audit_envelope(d,r['trace'],m)['verified']


@pytest.mark.parametrize('kind',['target','measurement','endpoint','history','inventory','localized','claim','budget'])
def test_rejects_resealed_semantic_tampering(tmp_path,kind):
    d=study();r=run_envelope(d,tmp_path/'packet');bad=copy.deepcopy(r['envelope']);e=bad['events'][0]
    if kind=='target':bad['target_within_monitored_limits']=True
    elif kind=='measurement':e['left']['value']+=.01
    elif kind=='endpoint':e['right']['result']['vm_pu'][1]+=.01
    elif kind=='history':e['history'][0]['arc_step']+=.001
    elif kind=='inventory':bad['events'].pop()
    elif kind=='localized':e['localized']=False
    elif kind=='claim':bad['protection_actions_simulated']=True
    else:bad['refinement_solves']+=1
    with pytest.raises(ValueError):audit_envelope(d,r['trace'],bad)


def test_invalid_current_units_unknown_branch_duplicate_and_offline_limits(tmp_path):
    for monitor in [dict(current_limits=[dict(branch_index=0,terminal='from',maximum_a=100)]),dict(current_limits=[dict(branch_index=99,terminal='from',maximum_pu=.2)]),dict(current_limits=[dict(branch_index=0,terminal='from',maximum_pu=.2)]*2)]:
        d=study();d['monitor']=monitor
        with pytest.raises(ValueError):run_envelope(d,tmp_path/'unused')
        assert not (tmp_path/'unused').exists()


def test_no_limits_and_unrated_branches_are_explicit(tmp_path):
    d=study();d['monitor']=dict(voltage_limits=False,apparent_power_limits=False)
    r=run_envelope(d,tmp_path/'packet');assert r['envelope']['completed'] and not r['envelope']['limits']
    assert r['envelope']['target_within_monitored_limits'] is True
    assert verify_envelope(tmp_path/'packet')['verified']


def test_q_limited_profile_monitoring_coexists_with_voltage_events(tmp_path):
    from tests.test_ac_reactive_arc import controlled,document
    a,b=controlled();a=replace(a,buses=[a.buses[0],replace(a.buses[1],vmin=.98)]);b=replace(b,buses=[a.buses[0],replace(a.buses[1],qd=.6)])
    d=document(a,b);r=run_envelope(d,tmp_path/'packet')
    assert r['trace']['control_changes'] and any(e['key']=='bus:1:voltage:lower' for e in r['envelope']['events'])
    assert verify_envelope(tmp_path/'packet')['verified']


def test_tapped_charged_parallel_offline_terminals_match_pandapower():
    import pandapower as pp
    from benchmarks.pseudo_arc_reference import ReferenceFamily
    from tests.test_balanced_ac_workflows import exact_case
    a,_=exact_case(tap=True);a=replace(a,branches=[*a.branches,a.branches[0],replace(a.branches[1],status=0)])
    vm=np.array([1.04,.98,1.01]);theta=np.array([0.,-.04,.02]);r=dict(vm_pu=vm.tolist(),va_deg=np.rad2deg(theta).tolist())
    measured=terminal_values(a,r)
    # Reference's makeYbus returns the independent terminal-admittance rows.
    from pandapower.pypower.makeYbus import makeYbus
    from pandapower.pypower.idx_brch import branch_cols
    idx={b.id:i for i,b in enumerate(a.buses)}
    bus=np.zeros((len(a.buses),13));bus[:,0]=np.arange(len(a.buses))
    branch=np.zeros((len(a.branches),branch_cols))
    for i,b in enumerate(a.branches):branch[i,:13]=[idx[b.fbus],idx[b.tbus],b.r,b.x,b.b,b.rate_a,0,0,b.ratio,b.angle_deg,b.status,b.angmin_deg,b.angmax_deg]
    _,yf,yt=makeYbus(a.base_mva,bus,branch);v=vm*np.exp(1j*theta)
    for k,b in enumerate(a.branches):
        assert measured[k]['from_current']==pytest.approx(abs((yf@v)[k]),abs=1e-12)
        assert measured[k]['to_current']==pytest.approx(abs((yt@v)[k]),abs=1e-12)
        assert measured[k]['from_mva']==pytest.approx(abs(v[idx[b.fbus]]*np.conj((yf@v)[k]))*a.base_mva,abs=1e-10)


def test_cli_and_schema_export(tmp_path,capsys):
    from power_grid_env.cli import main
    from power_grid_env.schema_export import export_schemas
    path=tmp_path/'input.json';path.write_text(json.dumps(study()));out=tmp_path/'packet'
    assert main(['ac-envelope','--input',str(path),'--out',str(out)])==0
    result=json.loads(capsys.readouterr().out);assert result['completed'] and not result['target_within_monitored_limits']
    assert main(['verify-ac-envelope',str(out)])==0
    assert json.loads(capsys.readouterr().out)['verified']
    export_schemas(tmp_path/'schemas');schema=json.loads((tmp_path/'schemas/ac-envelope-options.schema.json').read_text())
    assert 'current_limits' in schema['properties']


def test_overlapping_crossings_grouped_in_arc_order(tmp_path):
    d=study();d['monitor']['current_limits'][0]['maximum_pu']=.3
    r=run_envelope(d,tmp_path/'packet');m=r['envelope'];assert m['completed']
    first=m['event_groups'][m['first_detected_exit_group']]
    assert {m['events'][k]['key'] for k in first['event_indices']}=={'branch:0:current:from','branch:0:mva:from'}
    assert verify_envelope(tmp_path/'packet')['verified']


def test_failed_refinement_is_auditable_not_a_missing_event(tmp_path,monkeypatch):
    import power_grid_env.ac_envelope as module
    d=study();good=run_envelope(d,tmp_path/'packet')
    monkeypatch.setattr(module,'correct',lambda *args:(None,0,'test_newton_failure'))
    report=monitor_trace(d,good['trace']);assert report['reason']=='event_corrector_failed'
    assert audit_envelope(d,good['trace'],report)['verified']


def test_permutation_and_load_direction_do_not_change_terminal_physics(tmp_path):
    from power_grid_env.balanced_ac import case_from_document
    d=study();a=case_from_document(d['start']);b=case_from_document(d['target'])
    d['start']=case_document(replace(a,buses=a.buses[::-1],generators=a.generators[::-1]))
    d['target']=case_document(replace(b,buses=b.buses[::-1],generators=b.generators[::-1]))
    r=run_envelope(d,tmp_path/'packet');assert r['envelope']['completed'] and verify_envelope(tmp_path/'packet')['verified']
    assert len(r['envelope']['events'])==4


def test_upper_voltage_boundary_has_closed_form_capacitive_reference(tmp_path):
    a,b=analytic(.1);a=replace(a,buses=[a.buses[0],replace(a.buses[1],pd=0.,qd=-.01,vmax=1.1)])
    b=replace(a,buses=[a.buses[0],replace(a.buses[1],qd=-.3)])
    d=dict(start=case_document(a),target=case_document(b));r=run_envelope(d,tmp_path/'packet')
    assert r['envelope']['completed'] and len(r['envelope']['events'])==1
    e=r['envelope']['events'][0];assert e['key']=='bus:1:voltage:upper'
    exact=(-.11+.01)/(-.3+.01)
    assert e['left']['lambda_value']-1e-8<=exact<=e['right']['lambda_value']+1e-8
    assert verify_envelope(tmp_path/'packet')['verified']


def test_unreached_target_is_not_promoted_by_completed_monitor(tmp_path):
    a,b=analytic();d=dict(start=case_document(a),target=case_document(b));r=run_envelope(d,tmp_path/'packet')
    assert not r['trace']['completed'] and r['envelope']['completed']
    assert r['envelope']['target_within_monitored_limits'] is None
    v=verify_envelope(tmp_path/'packet');assert v['verified'] and not v['completed'] and not v['target_reached']


def test_zero_rate_is_unlimited_and_explicit_offline_current_rejected(tmp_path):
    from power_grid_env.balanced_ac import case_from_document
    d=study();a=case_from_document(d['start']);b=case_from_document(d['target'])
    branches=[replace(a.branches[0],rate_a=0.),replace(a.branches[0],status=0)]
    d['start']=case_document(replace(a,branches=branches));d['target']=case_document(replace(b,branches=branches));d['monitor']={}
    r=run_envelope(d,tmp_path/'packet');assert not any(x['kind']=='mva' for x in r['envelope']['limits'])
    assert verify_envelope(tmp_path/'packet')['verified']
    d['monitor']=dict(current_limits=[dict(branch_index=1,terminal='from',maximum_pu=.2)])
    with pytest.raises(ValueError):run_envelope(d,tmp_path/'unused')
    assert not (tmp_path/'unused').exists()


def test_cancel_is_honored_even_without_crossings(tmp_path):
    a,b=analytic(.2);d=dict(start=case_document(a),target=case_document(b),monitor=dict(voltage_limits=False,apparent_power_limits=False))
    r=run_envelope(d,tmp_path/'packet');report=monitor_trace(d,r['trace'],cancelled=lambda:True)
    assert report['reason']=='cancelled' and not report['completed']
    assert audit_envelope(d,r['trace'],report)['verified']
