from dataclasses import replace
import copy,math
import numpy as np
import pytest
from tests.test_ac_pseudo_arc import analytic
from tests.test_ac_dispatch import network,opts
from power_grid_env.ac_sensitivity import sensitivities,run_sensitivities,verify_sensitivities
from power_grid_env.ac_dispatch import solve_dispatch
from power_grid_env.balanced_ac import case_document,case_from_document
from power_grid_env.study_artifacts import encoded,read_json,seal,export_zip,extract_zip


def document():
    _,c=analytic(.3)
    return dict(case=case_document(c),dispatch=dict(participants=[dict(generator_index=0,weight=1.)]),directions=[dict(name='load',loads=[dict(bus_id=1,dp_mw=1.,dq_mvar=.2)])],outputs=[dict(name='v',kind='voltage',bus_id=1),dict(name='phase',kind='angle',bus_id=1),dict(name='p',kind='pg',generator_index=0),dict(name='slack',kind='delta')])


def test_exact_closed_form_voltage_gradient_and_forward_adjoint_identity():
    d=document();r=sensitivities(d);assert r['completed']
    v=r['state']['vm_pu'][1];gp=-.3/(v*.8);gq=-v/.8
    a=r['adjoint'][0]['load_gradients'][1]
    assert a['per_mw']==pytest.approx(gp,abs=1e-9);assert a['per_mvar']==pytest.approx(gq,abs=1e-9)
    assert r['forward'][0]['outputs']['v']==pytest.approx(gp+.2*gq,abs=1e-9)
    for metric in r['adjoint']:
        g=metric['load_gradients'][1]
        assert r['forward'][0]['outputs'][metric['name']]==pytest.approx(g['per_mw']+.2*g['per_mvar'],abs=1e-10)


@pytest.mark.parametrize('terminal',['from','to'])
def test_all_terminal_metrics_with_tap_phase_charging_and_offline_branch(terminal):
    from power_grid_env.ac_sensitivity import metric
    d=document();c=case_from_document(d['case']);line=replace(c.branches[0],r=.01,b=.02,ratio=1.03,angle_deg=3.)
    c=replace(c,branches=[line,replace(line,status=0)]);d['case']=case_document(c)
    d['outputs']=[dict(name=kind,kind=kind,branch_index=0,terminal=terminal) for kind in ['branch_p','branch_q','branch_mva','branch_current']]+[dict(name='offline',kind='branch_mva',branch_index=1,terminal=terminal)]
    r=sensitivities(d);assert r['completed'];eps=1e-5;values=[]
    for sign in [-1,1]:
        cc=replace(c,buses=[c.buses[0],replace(c.buses[1],pd=.3+sign*eps,qd=sign*eps*.2)]);dd=dict(d,case=case_document(cc));s=sensitivities(dd);assert s['completed'];values.append({a['name']:a['value'] for a in s['adjoint']})
    for output in d['outputs']:
        name=output['name'];fd=(values[1][name]-values[0][name])/(2*eps)
        assert r['forward'][0]['outputs'][name]==pytest.approx(fd,abs=1e-7)
    assert r['forward'][0]['outputs']['offline']==0.


def test_clamped_p_and_q_states_keep_fixed_active_set_derivative():
    c=network();c=replace(c,generators=[replace(c.generators[0],pmax=.22),*c.generators[1:]])
    d=dict(case=case_document(c),dispatch=opts(),directions=[dict(name='p',loads=[dict(bus_id=1,dp_mw=1.)])],outputs=[dict(name='v',kind='voltage',bus_id=0),dict(name='p0',kind='pg',generator_index=0)])
    r=sensitivities(d);assert r['completed'];assert r['state']['q_modes']['0']=='upper';assert r['forward'][0]['outputs']['p0']==0.
    values=[]
    for sign in [-1,1]:
        cc=replace(c,buses=[replace(b,pd=b.pd+sign*1e-5) if b.id==1 else b for b in c.buses]);s=solve_dispatch(cc,opts());assert s['completed'];values.append(s['result']['vm_pu'][0])
    assert r['forward'][0]['outputs']['v']==pytest.approx((values[1]-values[0])/2e-5,abs=1e-7)


def test_exact_p_limit_refuses_classical_derivative():
    c=network();c=replace(c,generators=[replace(c.generators[0],pmax=.25),*c.generators[1:]])
    d=dict(case=case_document(c),dispatch=opts(),outputs=[dict(name='v',kind='voltage',bus_id=1)])
    r=sensitivities(d);assert not r['completed'] and r['reason']=='active_set_boundary'
    assert any(p['kind']=='p' for p in r['guard']['near_boundaries']);assert not r['forward'] and not r['adjoint']


def test_supplied_state_is_audited_not_trusted():
    d=document();r=sensitivities(d);d['state']=copy.deepcopy(r['state']);d['state']['vm_pu'][1]*=.9
    with pytest.raises(ValueError):sensitivities(d)


@pytest.mark.parametrize('kind',['gradient','ranking','scope'])
def test_tampered_derivative_rejected_after_reseal(tmp_path,kind):
    out=tmp_path/'packet';run_sensitivities(document(),out);assert verify_sensitivities(out)['verified']
    r=read_json(out/'result.json')
    if kind=='gradient':r['adjoint'][0]['load_gradients'][1]['per_mw']+=.1
    elif kind=='ranking':r['adjoint'][0]['ranked_p_buses']=[]
    else:r['certified_trust_region']=True
    (out/'result.json').write_bytes(encoded(r));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError):verify_sensitivities(out)


def test_cli_and_zip(tmp_path,capsys):
    from power_grid_env.cli import main
    path=tmp_path/'input.json';path.write_bytes(encoded(document()))
    assert main(['ac-sensitivity','--input',str(path),'--out',str(tmp_path/'out')])==0
    assert main(['verify-ac-sensitivity',str(tmp_path/'out')])==0
    export_zip(tmp_path/'out',tmp_path/'out.zip');extract_zip(tmp_path/'out.zip',tmp_path/'restored');assert verify_sensitivities(tmp_path/'restored')['verified']


@pytest.mark.parametrize('change',[dict(outputs=[dict(name='x',kind='voltage',bus_id=99)]),dict(directions=[dict(name='x',loads=[dict(bus_id=99)])]),dict(outputs=[dict(name='x',kind='delta',bus_id=1)]),dict(switch_margin_pu=0.)])
def test_invalid_request_is_rejected(change):
    d=document();d.update(change)
    with pytest.raises(ValueError):sensitivities(d)


def test_exact_q_boundary_is_refused():
    c=network();wide=replace(c,generators=[replace(c.generators[0],qmin=-10.,qmax=10.),*c.generators[1:]])
    initial=solve_dispatch(wide,opts());assert initial['completed'];q=initial['result']['qg_mvar'][0]
    c=replace(wide,generators=[replace(wide.generators[0],qmax=q),*wide.generators[1:]])
    r=sensitivities(dict(case=case_document(c),dispatch=opts(),outputs=[dict(name='v',kind='voltage',bus_id=0)]))
    assert not r['completed'] and any(e['kind']=='q' for e in r['guard']['near_boundaries'])


def test_zero_width_constraints_are_smooth_not_switches():
    c=network();c=replace(c,generators=[replace(c.generators[0],pmin=.2,pmax=.2,qmin=.03,qmax=.03),*c.generators[1:]])
    r=sensitivities(dict(case=case_document(c),dispatch=opts(),outputs=[dict(name='p0',kind='pg',generator_index=0)]))
    assert r['completed'];assert all(g['per_mw']==0 and g['per_mvar']==0 for g in r['adjoint'][0]['load_gradients'])


def test_single_bus_units_and_gauge():
    from power_grid_env.network_opf import Bus,GeneratorSpec,OpfCase
    c=OpfCase('single',100.,[Bus(9,3,10.,0.,0.,0.,1.,0.,.9,1.1)],[GeneratorSpec(9,-100,100,-100,100,(0.,),pg=0.,qg=0.)],[])
    d=dict(case=case_document(c),dispatch=dict(participants=[dict(generator_index=0,weight=1.)]),outputs=[dict(name='p',kind='pg',generator_index=0),dict(name='angle',kind='angle',bus_id=9)],directions=[dict(name='p',loads=[dict(bus_id=9,dp_mw=1.)])]);r=sensitivities(d)
    assert r['completed'] and r['forward'][0]['outputs']['p']==pytest.approx(1.)
    assert r['forward'][0]['angle_rad_per_step']==[0.] and r['forward'][0]['outputs']['angle']==0.


def test_zero_magnitude_output_is_not_a_fake_smooth_derivative():
    d=document();c=case_from_document(d['case']);c=replace(c,buses=[replace(b,pd=0.,qd=0.) for b in c.buses]);d['case']=case_document(c);d['outputs']=[dict(name='zero',kind='branch_current',branch_index=0,terminal='from')]
    with pytest.raises(ValueError,match='not differentiable'):sensitivities(d)


def test_independent_nonlinear_perturbations_and_inventory_guard(monkeypatch):
    import benchmarks.ac_sensitivity_acceptance as bench
    from benchmarks.ac_dispatch_sparse_reference import solve_reference
    from power_grid_env.ac_dispatch import DispatchSystem
    d=document();c=case_from_document(d['case']);d['directions']=bench.selected_directions(c,1);r=sensitivities(d);assert r['completed']
    def forbidden(*a,**kw):raise AssertionError('primary equation called by independent reference')
    monkeypatch.setattr(DispatchSystem,'evaluate',forbidden)
    seed=solve_reference(c,d['dispatch']);base=bench.independent_solve(c,d['dispatch'],seed);records=[]
    for direction,h in bench.record_design(d,r):
        refs=[bench.independent_solve(bench.changed(c,direction,sign*h),d['dispatch'],base) for sign in [-1,1]]
        records.append(dict(direction=direction,step=h,references=refs))
    assert bench.validation(d,r,base,records)['passed']
    with pytest.raises(ValueError,match='inventory'):bench.validation(d,r,base,records[:-1])
    broken=copy.deepcopy(records);broken[0]['references'][0]['result']['branch_power_mva'][0][0]+=.01
    with pytest.raises(ValueError):bench.validation(d,r,base,broken)


def test_angle_gauge_preserves_physical_derivatives():
    d=document();d['outputs'].append(dict(name='current',kind='branch_current',branch_index=0,terminal='to'))
    a=sensitivities(d);d['dispatch']['angle_reference_bus']=1;b=sensitivities(d);assert a['completed'] and b['completed']
    for left,right in zip(a['adjoint'],b['adjoint']):
        if left['kind']=='angle':continue
        for x,y in zip(left['load_gradients'],right['load_gradients']):
            assert x['per_mw']==pytest.approx(y['per_mw'],abs=1e-9);assert x['per_mvar']==pytest.approx(y['per_mvar'],abs=1e-9)


def test_2048_bus_native_forward_adjoint_without_dense_conversion(monkeypatch):
    from benchmarks.ac_scale_acceptance import synthetic
    from scipy.sparse import csc_matrix,csr_matrix
    def forbidden(*a,**kw):raise AssertionError('dense conversion in sparse sensitivity')
    monkeypatch.setattr(csc_matrix,'toarray',forbidden);monkeypatch.setattr(csr_matrix,'toarray',forbidden)
    c=synthetic(2048);d=dict(case=case_document(c),dispatch=dict(participants=[dict(generator_index=i,weight=1.) for i in range(len(c.generators))]),directions=[dict(name='p',loads=[dict(bus_id=2047,dp_mw=1.)])],outputs=[dict(name='v',kind='voltage',bus_id=2047)])
    r=sensitivities(d);assert r['completed'];assert len(r['adjoint'][0]['load_gradients'])==2048
    assert r['forward'][0]['outputs']['v']==pytest.approx(r['adjoint'][0]['load_gradients'][-1]['per_mw'],abs=1e-9)
