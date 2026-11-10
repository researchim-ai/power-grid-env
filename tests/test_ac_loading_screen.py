from dataclasses import replace
import copy,math
import numpy as np
import pytest
from tests.test_ac_pseudo_arc import analytic
from power_grid_env.ac_loading_screen import run_loading_screen,verify_loading_screen,study_input
from power_grid_env.balanced_ac import case_document,case_from_document
from power_grid_env.study_artifacts import encoded,read_json,seal,export_zip,extract_zip
from benchmarks.ac_independent_arc import ReferenceArc


def document(target=.7,step=.04):
    _,c=analytic(target)
    return dict(case=case_document(c),starting_load_factor=.1/target,requested_load_factor=1.,options=dict(enforce_q_limits=True,dispatch=dict(participants=[dict(generator_index=0,weight=1.)]),step=step,maximum_step=step),monitor={})


@pytest.mark.parametrize('step',[.04,.02])
def test_closed_form_fold_is_observation_not_absence_certificate(tmp_path,step):
    d=document(step=step);r=run_loading_screen(d,tmp_path/'scan')
    assert r['completed'] and not r['requested_target_reached']
    assert r['reason']=='sampled_turn_before_target'
    assert r['sampled_turns'][0]['load_factor']==pytest.approx(.5/.7,abs=2e-7)
    assert not r['stability_proven'] and not r['global_target_absence_proven'] and not r['certified_loading_margin']
    assert verify_loading_screen(tmp_path/'scan')['verified']


def test_target_reached_is_separate_from_screen_completion(tmp_path):
    r=run_loading_screen(document(.3),tmp_path/'scan')
    assert r['completed'] and r['requested_target_reached'] and not r['sampled_turns']
    assert r['reason']=='requested_target_reached'


@pytest.mark.parametrize('kind',['budget','cancel'])
def test_no_false_initial_feasibility_or_target_on_early_stop(tmp_path,kind):
    d=document()
    if kind=='budget':d['options']['maximum_wall_s']=1e-12
    r=run_loading_screen(d,tmp_path/'scan',cancelled=(lambda:True) if kind=='cancel' else None)
    assert not r['completed'] and not r['requested_target_reached']
    assert r['starting_operating_limits_satisfied'] is None
    assert verify_loading_screen(tmp_path/'scan')['verified']


@pytest.mark.parametrize('kind',['absence','factor','turn','claim'])
def test_semantic_tampering_after_reseal_rejected(tmp_path,kind):
    packet=tmp_path/'scan';run_loading_screen(document(),packet);r=read_json(packet/'result.json')
    if kind=='absence':r['global_target_absence_proven']=True
    elif kind=='factor':r['maximum_sampled_load_factor']+=.1
    elif kind=='turn':r['sampled_turns'][0]['load_factor']+=.1
    else:r['requested_target_reached']=True
    (packet/'result.json').write_bytes(encoded(r));(packet/'manifest.json').unlink();seal(packet)
    with pytest.raises(ValueError):verify_loading_screen(packet)


def test_zip_and_cli(tmp_path,capsys):
    from power_grid_env.cli import main
    path=tmp_path/'input.json';path.write_bytes(encoded(document()))
    assert main(['ac-loading-screen','--input',str(path),'--out',str(tmp_path/'scan')])==0
    assert main(['verify-ac-loading-screen',str(tmp_path/'scan')])==0
    export_zip(tmp_path/'scan',tmp_path/'scan.zip');extract_zip(tmp_path/'scan.zip',tmp_path/'restored')
    assert verify_loading_screen(tmp_path/'restored')['verified']


@pytest.mark.parametrize('changes',[dict(starting_load_factor=1.),dict(requested_load_factor=0.),dict(options=dict(enforce_q_limits=False)),dict(unsupported=True)])
def test_unqualified_policy_or_malformed_input_rejected(changes):
    d=document();d.update(changes)
    with pytest.raises(ValueError):study_input(d)


def test_separate_reference_crosses_exact_fold_and_tracks_both_solutions(monkeypatch):
    from power_grid_env.ac_dispatch import DispatchSystem
    import power_grid_env.ac_pseudo_arc as native
    def forbidden(*a,**kw):raise AssertionError('native controller or equations used')
    monkeypatch.setattr(DispatchSystem,'evaluate',forbidden);monkeypatch.setattr(native,'correct',forbidden)
    d=document();x=study_input(d);f=ReferenceArc(case_from_document(x['start']),case_from_document(x['target']),d['options']['dispatch']);path=f.trace(d['options']['dispatch'],step=.02)
    assert path['turn']['u'][-1]==pytest.approx(2/3,abs=1e-7)
    for side in [1,-1]:
        r=f.at_lambda(path,.65,side);p=.1+.6*.65;voltage=complex(math.cos(r['result']['angle_rad'][1]),math.sin(r['result']['angle_rad'][1]))*r['result']['voltage_pu'][1]
        assert voltage.imag==pytest.approx(-p,abs=1e-9)
        assert voltage.real**2-voltage.real+p*p==pytest.approx(0,abs=1e-9)
        assert (r['result']['voltage_pu'][1]>1/math.sqrt(2))==(side==1)


def test_separate_reference_loading_column_matches_finite_difference():
    d=document();x=study_input(d);f=ReferenceArc(case_from_document(x['start']),case_from_document(x['target']),d['options']['dispatch']);u,t=f.initial(d['options']['dispatch']);u[-1]=.3;_,a=f.evaluate(u)
    direction=np.random.default_rng(8).normal(size=len(u));eps=1e-6
    np.testing.assert_allclose(a@direction,(f.evaluate(u+eps*direction)[0]-f.evaluate(u-eps*direction)[0])/(2*eps),atol=1e-8,rtol=1e-7)


@pytest.mark.parametrize('kind',['tangent','comparison'])
def test_full_independent_evidence_rejects_resealed_tampering(tmp_path,monkeypatch,kind):
    import benchmarks.ac_loading_acceptance as bench
    monkeypatch.setattr(bench,'document',lambda step:document(step=step));monkeypatch.setattr(bench,'sources',lambda:{'test':'fixed'})
    out=tmp_path/'assessment';assert bench.qualify(out)['passed'];assert bench.verify_qualification(out)['verified']
    evidence=out/'0.03-evidence'
    if kind=='tangent':
        path=evidence/'reference-path.json';r=read_json(path);r['turn']['tangent'][0]+=.1
    else:
        path=evidence/'references.json';r=read_json(path);r[0]['reference']['result']['pg_mw'][0]+=.1
    path.write_bytes(encoded(r));(evidence/'manifest.json').unlink();seal(evidence);(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError):bench.verify_qualification(out)
