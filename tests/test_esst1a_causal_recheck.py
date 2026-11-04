"""Pinned historical evidence is not trusted merely because its delivery passed."""
import json
from pathlib import Path
import pytest
from benchmarks import generator_esst1a_causal_recheck as m
from power_grid_env.evidence import package_fingerprint


@pytest.fixture
def archive(tmp_path,monkeypatch):
    workspace=tmp_path/'workspace';workspace.mkdir();corpus=workspace/'corpus';sealed=workspace/'sealed';sealed.mkdir();manifest={}
    monkeypatch.setattr(m,'ROOT',workspace)
    def put(relative,value):
        p=corpus/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));manifest[p.relative_to(workspace).as_posix()]=m.sha(p)
    put('protocol.json',dict(code_sha256=package_fingerprint(),native_UEL_event_policy='causal',cases={'hold':m.case('hold').model_dump(mode='json')},native_tolerances=[1e-11,1e-12]))
    put('summary.json',dict(status='complete',runs=[dict(case='hold',tolerance=t) for t in (1e-11,1e-12)]))
    put('hold/primary.json',dict(completed=True))
    for t in [1e-11,1e-12]:put('hold/'+str(t)+'/reference.json',dict(completed=True,native_UEL_event_policy='causal',native_MNLEX2_in_feedback=True,event_log=[]))
    def seal(**changes):
        index=sealed/'external-evidence-sha256.json';index.write_text(json.dumps(manifest))
        r=dict(passed=True,sealed=True,production_code_sha256=package_fingerprint(),artifacts_sha256={index.name:m.sha(index)});r.update(changes)
        delivery=sealed/'delivery.json';delivery.write_text(json.dumps(r));return m.sha(delivery)
    monkeypatch.setattr(m,'compare',lambda *args:dict(passed=True))
    return sealed/'delivery.json',corpus,put,seal


def test_no_native_execution_is_needed_for_a_verified_guard_recheck(archive,tmp_path,monkeypatch):
    from benchmarks import esst1a_network,mnlex2_model_exchange
    def forbidden(*args,**kwargs):raise AssertionError('historical recheck attempted native execution')
    monkeypatch.setattr(esst1a_network,'run',forbidden);monkeypatch.setattr(mnlex2_model_exchange.NativeUEL,'__init__',forbidden)
    delivery,corpus,_,seal=archive;r=m.recheck(delivery,corpus,tmp_path/'out',seal())
    assert r['passed'] and r['audit_completed'] and not r['native_simulations_rerun']
    assert len(r['consumed_inputs_sha256'])==5 and len(r['runs'])==2
    assert not r['industrial_profile_qualified']


@pytest.mark.parametrize('bad',['pin','index','unsealed','production','protocol_production','passport','matrix','duplicate_matrix','input','broadcast','missing_guard','nonfinite','escape'])
def test_incomplete_changed_or_semantically_invalid_evidence_is_rejected(archive,tmp_path,bad):
    delivery,corpus,put,seal=archive;changes={}
    if bad=='unsealed':changes['sealed']=False
    elif bad=='production':changes['production_code_sha256']='0'*64
    elif bad in ('protocol_production','passport'):
        p=json.loads((corpus/'protocol.json').read_text())
        if bad=='protocol_production':p['code_sha256']='0'*64
        else:p['cases']['hold']['id']='modified-passport'
        put('protocol.json',p)
    elif bad in ('matrix','duplicate_matrix'):
        p=json.loads((corpus/'summary.json').read_text());p['runs'].pop() if bad=='matrix' else p['runs'].append(p['runs'][0]);put('summary.json',p)
    elif bad in ('broadcast','missing_guard','nonfinite'):
        b=json.loads((corpus/'hold/1e-11/reference.json').read_text())
        if bad=='broadcast':b['native_UEL_event_policy']='broadcast'
        elif bad=='missing_guard':b['event_log']=[dict(kind='native_mnlex2_lag_reset')]
        else:b['unrelated_nonfinite']=float('nan')
        put('hold/1e-11/reference.json',b)
    pin=seal(**changes)
    if bad=='pin':pin='0'*64
    elif bad=='index':(delivery.parent/'external-evidence-sha256.json').write_text('{}')
    elif bad=='input':(corpus/'hold/primary.json').write_text('{}')
    elif bad=='escape':
        external=tmp_path/'external';external.write_text((corpus/'hold/primary.json').read_text());(corpus/'hold/primary.json').unlink();(corpus/'hold/primary.json').symlink_to(external)
    with pytest.raises(ValueError):m.recheck(delivery,corpus,tmp_path/'out',pin)


def test_failed_cross_solver_comparison_stays_failed_after_successful_guard_audit(archive,tmp_path,monkeypatch):
    delivery,corpus,_,seal=archive;monkeypatch.setattr(m,'compare',lambda *args:dict(passed=False))
    r=m.recheck(delivery,corpus,tmp_path/'out',seal())
    assert r['audit_completed'] and not r['passed']
    assert all(row['guard_audit_passed'] and not row['passed'] for row in r['runs'])


def test_source_change_during_recheck_prevents_publication(archive,tmp_path,monkeypatch):
    from benchmarks import native_online_validation
    delivery,corpus,_,seal=archive;calls=[]
    def identity():calls.append(1);return len(calls)
    monkeypatch.setattr(native_online_validation,'source_identity',identity)
    with pytest.raises(ValueError,match='producer changed'):m.recheck(delivery,corpus,tmp_path/'out',seal())
    assert not json.loads((tmp_path/'out/summary.json').read_text())['audit_completed']


def test_old_input_changed_during_recheck_prevents_publication(archive,tmp_path,monkeypatch):
    delivery,corpus,_,seal=archive;calls=[]
    def compare(*args):
        calls.append(1)
        if len(calls)==2:(corpus/'hold/primary.json').write_text('{}')
        return dict(passed=True)
    monkeypatch.setattr(m,'compare',compare)
    with pytest.raises(ValueError,match='evidence changed'):m.recheck(delivery,corpus,tmp_path/'out',seal())
    assert not json.loads((tmp_path/'out/summary.json').read_text())['audit_completed']


def test_historical_artifacts_and_existing_output_are_not_overwritten(archive,tmp_path):
    delivery,corpus,_,seal=archive;pin=seal()
    with pytest.raises(ValueError,match='outside'):m.recheck(delivery,corpus,corpus/'out',pin)
    out=tmp_path/'out';out.mkdir();p=out/'summary.json';p.write_text('retain')
    with pytest.raises(FileExistsError):m.recheck(delivery,corpus,out,pin)
    assert p.read_text()=='retain' and m.sha(delivery)==pin
