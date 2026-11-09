from dataclasses import replace
import copy,math
import numpy as np
import pytest
from power_grid_env.network_opf import Bus,Branch,GeneratorSpec,OpfCase
from power_grid_env.balanced_ac import case_document,solve_power_flow
from power_grid_env.ac_pseudo_arc import PolarFamily,trace_power_flow,run_pseudo_arc,verify_pseudo_arc
from power_grid_env.study_artifacts import encoded,read_json,seal,export_zip,extract_zip


def analytic(target=.7):
    a=OpfCase('two-bus-closed-form',1.,[Bus(0,3,0.,0.,0.,0.,1.,0.,.02,2.),Bus(1,1,.1,0.,0.,0.,1.,0.,.02,2.)],
        [GeneratorSpec(0,-100,100,-100,100,(0.,),pg=0.,qg=0.)],[Branch(0,1,0.,1.,0.,100.,0.,0.,1,-180,180)])
    return a,replace(a,buses=[a.buses[0],replace(a.buses[1],pd=target)])


def test_crosses_analytic_fold_and_follows_lower_voltage_branch():
    a,b=analytic();r=trace_power_flow(a,b)
    assert not r['completed'] and r['reason']=='sampled_turn_followed' and len(r['turns'])==1
    assert any(p['tangent'][-1]<0 for p in r['points'])
    assert r['points'][-1]['result']['vm_pu'][1]<1/math.sqrt(2)
    for point in r['points']:
        p=.1+.6*point['lambda_value'];v=point['result']['vm_pu'][1]*np.exp(1j*np.deg2rad(point['result']['va_deg'][1]))
        assert v.imag==pytest.approx(-p,abs=1e-9)
        assert v.real*v.real-v.real+p*p==pytest.approx(0,abs=1e-9)
    estimate=r['turns'][0]['refinement']['estimate']
    assert estimate['lambda_value']==pytest.approx(2/3,abs=1e-7)
    assert not r['target_absence_proven'] and not r['stability_proven']


@pytest.mark.parametrize('step',[.04,.02])
def test_refined_turn_agrees_with_exact_maximum_when_step_halves(step):
    a,b=analytic();r=trace_power_flow(a,b,dict(step=step,maximum_step=step))
    estimate=r['turns'][0]['refinement']['estimate']
    assert estimate['lambda_value']==pytest.approx(2/3,abs=1e-7)
    assert estimate['result']['vm_pu'][1]==pytest.approx(1/math.sqrt(2),abs=2e-5)


def test_exact_target_refinement_uses_target_loads_not_overshot_predictor():
    a,b=analytic(.3);r=trace_power_flow(a,b)
    assert r['completed'] and r['reason']=='target_reached'
    assert r['final_result']['vm_pu'][1]==pytest.approx(math.sqrt(.9),abs=1e-9)
    assert r['final_result']['pg_mw'][0]==pytest.approx(.3,abs=1e-9)


def test_augmented_jacobian_and_lambda_column_match_finite_differences():
    a,b=analytic();f=PolarFamily(a,b);u=f.pack(solve_power_flow(a),.2);_,j=f.evaluate(u,True)
    for i in range(len(u)):
        d=np.zeros(len(u));d[i]=1e-6;measured=(f.evaluate(u+d)-f.evaluate(u-d))/2e-6
        assert measured==pytest.approx(j.toarray()[:,i] if i<f.size else f.g,abs=1e-8)


def test_rejected_corrector_rolls_back_without_adding_false_points(monkeypatch):
    import power_grid_env.ac_pseudo_arc as module
    real=module.correct;count=[0]
    def fail_once(*args):
        count[0]+=1
        return (None,0,'corrector_iteration_budget') if count[0]==1 else real(*args)
    monkeypatch.setattr(module,'correct',fail_once)
    a,b=analytic(.3);r=trace_power_flow(a,b)
    assert r['completed'] and not r['attempts'][0]['accepted']
    assert r['attempts'][0]['anchor']==r['attempts'][1]['anchor']==0
    assert r['attempts'][1]['step']==r['attempts'][0]['step']/2


@pytest.mark.parametrize('options,reason',[(dict(maximum_points=2),'maximum_points'),(dict(maximum_attempts=1),'maximum_attempts'),(dict(maximum_wall_s=1e-12),'wall_deadline')])
def test_incomplete_budget_cannot_become_proof_of_no_target(options,reason):
    a,b=analytic();r=trace_power_flow(a,b,options)
    assert not r['completed'] and r['reason']==reason and r['final_result'] is None
    assert not r['target_absence_proven']


def test_cancel_before_newton_with_both_control_profiles(monkeypatch):
    import power_grid_env.ac_pseudo_arc as module
    a,b=analytic()
    def forbidden(*a,**k):raise AssertionError('Newton called after cancellation')
    monkeypatch.setattr(module,'solve_power_flow',forbidden)
    assert trace_power_flow(a,b,cancelled=lambda:True)['reason']=='cancelled'
    assert trace_power_flow(a,b,dict(enforce_q_limits=True),cancelled=lambda:True)['reason']=='cancelled'


@pytest.fixture(scope='module')
def packet(tmp_path_factory):
    a,b=analytic();out=tmp_path_factory.mktemp('arc')/'packet'
    run_pseudo_arc(dict(start=case_document(a),target=case_document(b)),out);return out


def test_partial_branch_artifact_and_zip_verified(packet,tmp_path):
    a=verify_pseudo_arc(packet);assert a['verified'] and not a['completed'] and a['sampled_turns']==1
    export_zip(packet,tmp_path/'arc.zip');extract_zip(tmp_path/'arc.zip',tmp_path/'restored')
    assert verify_pseudo_arc(tmp_path/'restored')==a


@pytest.mark.parametrize('kind',['tangent','state','arc','anchor','turn','absence_claim'])
def test_resealed_curve_tamper_is_detected(packet,tmp_path,kind):
    import shutil
    out=tmp_path/'forged';shutil.copytree(packet,out);r=read_json(out/'result.json')
    if kind=='tangent':r['points'][2]['tangent'][-1]*=-1
    elif kind=='state':r['points'][2]['result']['vm_pu'][1]+=.1
    elif kind=='arc':r['points'][2]['arc_step']*=2
    elif kind=='anchor':r['attempts'][1]['anchor']=0
    elif kind=='turn':r['turns']=[]
    else:r['target_absence_proven']=True
    (out/'result.json').write_bytes(encoded(r));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError):verify_pseudo_arc(out)


def test_cli_and_schema(packet,tmp_path,capsys):
    from power_grid_env.cli import main
    assert main(['verify-ac-pseudo-arc',str(packet)])==2
    assert main(['schemas','--out',str(tmp_path/'schema')])==0
    assert read_json(tmp_path/'schema/ac-pseudo-arc-options.schema.json')['title']=='PseudoArcOptions'


def test_pinned_reference_equations_do_not_use_production_stamps_or_derivatives(monkeypatch):
    import power_grid_env.ac_pseudo_arc as native
    from benchmarks.pseudo_arc_reference import ReferenceFamily
    a,b=analytic();model=PolarFamily(a,b);u=model.pack(solve_power_flow(a),.2);f,j=model.evaluate(u,True)
    def forbidden(*a,**k):raise AssertionError('native electrical implementation reused')
    monkeypatch.setattr(native,'admittance',forbidden);monkeypatch.setattr(native,'derivatives',forbidden)
    other=ReferenceFamily(a,b);g,k=other.evaluate(u,True)
    assert f==pytest.approx(g,abs=1e-12) and j.toarray()==pytest.approx(k.toarray(),abs=1e-12)


def test_global_phase_reference_does_not_change_the_branch():
    a,b=analytic();a=replace(a,buses=[replace(bus,va_deg=bus.va_deg+30) for bus in a.buses]);b=replace(b,buses=[replace(bus,va_deg=bus.va_deg+30) for bus in b.buses])
    r=trace_power_flow(a,b)
    assert r['turns'][0]['refinement']['estimate']['lambda_value']==pytest.approx(2/3,abs=1e-7)


def test_invalid_branch_stop_cannot_be_resealed_as_a_proof(packet,tmp_path):
    import shutil
    out=tmp_path/'invalid-stop';shutil.copytree(packet,out);r=read_json(out/'result.json');r['reason']='proven_no_solution'
    (out/'result.json').write_bytes(encoded(r));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError):verify_pseudo_arc(out)
