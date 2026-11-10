from dataclasses import replace
import copy
import math
import numpy as np
import pytest
from scipy.optimize import minimize
from power_grid_env.ac_schedule import prepare_schedule,run_schedule,verify_schedule
from power_grid_env.balanced_ac import case_document,case_from_document
from power_grid_env.study_artifacts import encoded,read_json,seal,export_zip,extract_zip
from tests.test_ac_dispatch import network


def options():return dict(participants=[dict(generator_index=i,weight=w) for i,w in enumerate([1.,2.,1.])])


def invalid_case():
    c=network();return replace(c,generators=[replace(c.generators[0],pg=-.1),replace(c.generators[1],pg=.6,pmax=.5),replace(c.generators[2],pg=.2)])


def test_projection_matches_independent_constrained_optimizer_and_kkt():
    c=invalid_case();before=case_document(c);r=prepare_schedule(c,options());assert r['completed']
    original=np.array([g.pg for g in c.generators]);weights=np.array([1.,2.,1.]);actual=np.array([g['pg'] for g in r['prepared_case']['generators']])
    ref=minimize(lambda p:sum((p-original)**2/weights)/2,np.array([.1,.4,.2]),jac=lambda p:(p-original)/weights,bounds=[(g.pmin,g.pmax) for g in c.generators],constraints=[dict(type='eq',fun=lambda p:sum(p)-sum(original),jac=lambda p:np.ones(3))],method='SLSQP',options=dict(ftol=1e-12,maxiter=100))
    assert ref.success;np.testing.assert_allclose(actual,ref.x,atol=1e-8,rtol=0)
    assert abs(sum(actual)-sum(original))<1e-8
    assert case_document(c)==before
    prepared=case_from_document(r['prepared_case'])
    assert replace(prepared,generators=c.generators)==c
    for a,b in zip(c.generators,prepared.generators):assert replace(b,pg=a.pg)==a
    assert not r['electrical_feasibility_proven']


def test_valid_schedule_is_exactly_unchanged_and_repeatable():
    c=network();r=prepare_schedule(c,options());assert r['prepared_case']==case_document(c)
    assert prepare_schedule(case_from_document(r['prepared_case']),options())==r


@pytest.mark.parametrize('target',[0.,3.])
def test_exact_aggregate_endpoints(target):
    c=network();o=options();o['target_total_mw']=target;r=prepare_schedule(c,o)
    assert r['completed'];assert abs(sum(g['pg'] for g in r['prepared_case']['generators'])-target)<=1e-8


@pytest.mark.parametrize('target',[-.01,3.01])
def test_infeasible_total_has_no_candidate(target):
    o=options();o['target_total_mw']=target;r=prepare_schedule(network(),o)
    assert not r['completed'] and r['prepared_case'] is None and not r['changes']


def test_nonadjustable_units_are_never_repaired_silently():
    c=invalid_case()
    with pytest.raises(ValueError,match='nonadjustable'):prepare_schedule(c,dict(participants=[dict(generator_index=2,weight=1.)]))
    c=network();o=dict(participants=[dict(generator_index=1,weight=1.)],target_total_mw=.7)
    r=prepare_schedule(c,o);assert r['completed']
    assert r['prepared_case']['generators'][0]['pg']==c.generators[0].pg
    assert r['prepared_case']['generators'][2]['pg']==c.generators[2].pg


@pytest.mark.parametrize('bad',[dict(participants=[dict(generator_index=0,weight=0.)]),dict(participants=[dict(generator_index=0,weight=1.)]*2),dict(participants=[dict(generator_index=100,weight=1.)]),dict(participants=[dict(generator_index=0,weight=1.)],target_total_mw=float('nan'))])
def test_invalid_configuration_rejected(bad):
    with pytest.raises(ValueError):prepare_schedule(network(),bad)


def test_permutation_and_weight_scaling_preserve_solution():
    c=invalid_case();o=options();r=prepare_schedule(c,o)
    cc=replace(c,generators=c.generators[::-1]);oo=dict(participants=[dict(generator_index=2-p['generator_index'],weight=p['weight']*10) for p in o['participants']]);other=prepare_schedule(cc,oo)
    assert [g['pg'] for g in r['prepared_case']['generators']]==pytest.approx([g['pg'] for g in other['prepared_case']['generators']][::-1],abs=1e-8)


@pytest.mark.parametrize('kind',['bounds','schedule','claim','total'])
def test_resealed_semantic_tampering_rejected(tmp_path,kind):
    packet=tmp_path/'packet';run_schedule(dict(case=case_document(invalid_case()),options=options()),packet);assert verify_schedule(packet)['verified']
    export_zip(packet,tmp_path/'packet.zip');extract_zip(tmp_path/'packet.zip',tmp_path/'restored');assert verify_schedule(tmp_path/'restored')['verified']
    r=read_json(packet/'result.json')
    if kind=='bounds':r['prepared_case']['generators'][0]['pmax']+=1.
    elif kind=='schedule':r['prepared_case']['generators'][0]['pg']+=.01
    elif kind=='claim':r['electrical_feasibility_proven']=True
    else:r['target_total_mw']+=1.
    (packet/'result.json').write_bytes(encoded(r));(packet/'manifest.json').unlink();seal(packet)
    with pytest.raises(ValueError):verify_schedule(packet)


def test_cli_success_infeasible_and_verification(tmp_path,capsys):
    from power_grid_env.cli import main
    path=tmp_path/'input.json';path.write_bytes(encoded(dict(case=case_document(invalid_case()),options=options())))
    assert main(['ac-prepare-schedule','--input',str(path),'--out',str(tmp_path/'out')])==0
    assert main(['verify-ac-schedule',str(tmp_path/'out')])==0
    assert not __import__('json').loads(capsys.readouterr().out.splitlines()[-1])['electrical_feasibility_proven']


def test_fixed_width_units_and_declared_total():
    c=network();c=replace(c,generators=[replace(c.generators[0],pmin=.2,pmax=.2),*c.generators[1:]])
    r=prepare_schedule(c,dict(options(),target_total_mw=.8));assert r['completed']
    assert r['prepared_case']['generators'][0]['pg']==.2
    assert sum(g['pg'] for g in r['prepared_case']['generators'])==pytest.approx(.8,abs=1e-8)


def test_full_admission_evidence_rejects_resealed_rejection_claim(tmp_path,monkeypatch):
    import benchmarks.ac_schedule_acceptance as bench
    c=invalid_case();source=tmp_path/'source.json';source.write_bytes(encoded(dict(options=dict(dispatch=options()))))
    monkeypatch.setattr(bench,'SOURCE',source);monkeypatch.setattr(bench,'sources',lambda:{'test':'fixed'})
    monkeypatch.setattr(bench,'schedule_input',lambda:dict(case=case_document(c),options=options()))
    def derived(r,step):
        a=case_from_document(r['prepared_case']);b=replace(a,buses=[replace(bus,pd=bus.pd*1.01) for bus in a.buses])
        return dict(start=case_document(a),target=case_document(b),options=dict(enforce_q_limits=True,dispatch=options(),step=step,maximum_step=step),monitor={})
    monkeypatch.setattr(bench,'study_input',derived)
    out=tmp_path/'assessment';summary=bench.qualify(out)
    assert summary['passed'];assert bench.verify_qualification(out)['verified']
    s=read_json(out/'summary.json');s['original_schedule_rejected']='made-up failure'
    (out/'summary.json').write_bytes(encoded(s));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError,match='original rejection differs'):bench.verify_qualification(out)


@pytest.mark.parametrize('name,completed',[('redistribute',True),('infeasible-total',False)])
def test_shipped_examples_are_reproducible(tmp_path,name,completed):
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'cases/ac-schedule'/f'{name}.json'
    d=read_json(path);r=run_schedule(d,tmp_path/name)
    assert r['completed']==completed;assert verify_schedule(tmp_path/name)['completed']==completed
