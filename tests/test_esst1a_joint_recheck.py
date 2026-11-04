"""Historical evidence must be pinned, complete, unchanged and honestly labelled."""
import json
import pytest
from benchmarks import generator_esst1a_joint_recheck as module
from benchmarks.generator_esst1a_joint_cases import NAMES,example
from power_grid_env.evidence import package_fingerprint


@pytest.fixture
def archive(tmp_path,monkeypatch):
    root=tmp_path/'sealed';root.mkdir();artifacts={}
    def put(relative,value):
        p=root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value))
        artifacts[relative]=module.digest(p)
    put('network/protocol.json',dict(cases={n:example(n).model_dump(mode='json') for n in NAMES},native_tolerances=[1e-11,1e-12],code_sha256=package_fingerprint()))
    for n in NAMES:
        put('network/'+n+'/primary.json',dict(name=n))
        for t in (1e-11,1e-12):put('network/'+n+'/'+str(t)+'/reference.json',dict(name=n))
    def seal(**changes):
        r=dict(sealed=True,passed=True,production_code_sha256=package_fingerprint(),artifacts_sha256=artifacts);r.update(changes)
        (root/'delivery.json').write_text(json.dumps(r));return module.digest(root/'delivery.json')
    monkeypatch.setattr(module,'compare',lambda s,a,b:dict(passed=True,native_UEL_reset_checks=[],reset_failures=[]))
    return root,put,seal


def test_recheck_consumes_only_pinned_inputs_and_never_claims_new_simulations(archive,tmp_path):
    root,_,seal=archive;pin=seal();r=module.recheck(root,tmp_path/'result',pin)
    assert r['passed'] and len(r['runs'])==24 and len(r['consumed_artifacts_sha256'])==37
    assert r['native_simulations_rerun'] is False and r['industrial_profile_qualified'] is False
    assert r['historical_delivery_sha256']==pin and module.digest(root/'delivery.json')==pin


@pytest.mark.parametrize('bad',['delivery_pin','unsealed','failed','production','protocol_production','artifact','missing_manifest_entry','case_passport','missing_case','missing_tolerance','nonfinite','escape'])
def test_recheck_rejects_incomplete_changed_or_untrusted_evidence(archive,tmp_path,bad):
    root,put,seal=archive;changes={}
    protocol=json.loads((root/'network/protocol.json').read_text())
    if bad=='unsealed':changes['sealed']=False
    elif bad=='failed':changes['passed']=False
    elif bad=='production':changes['production_code_sha256']='0'*64
    elif bad=='protocol_production':protocol['code_sha256']='0'*64
    elif bad=='missing_case':del protocol['cases'][NAMES[0]]
    elif bad=='missing_tolerance':protocol['native_tolerances']=[1e-11]
    elif bad=='case_passport':protocol['cases'][NAMES[0]]['id']='different-passport'
    if bad in ('protocol_production','missing_case','missing_tolerance','case_passport'):put('network/protocol.json',protocol)
    if bad=='nonfinite':put('network/hold/primary.json',dict(value=float('nan')))
    pin=seal(**changes)
    if bad=='delivery_pin':pin='0'*64
    elif bad=='artifact':(root/'network/hold/primary.json').write_text('{}')
    elif bad=='missing_manifest_entry':
        r=json.loads((root/'delivery.json').read_text());del r['artifacts_sha256']['network/hold/primary.json'];(root/'delivery.json').write_text(json.dumps(r));pin=module.digest(root/'delivery.json')
    elif bad=='escape':
        external=tmp_path/'external';external.write_text((root/'network/hold/primary.json').read_text());(root/'network/hold/primary.json').unlink();(root/'network/hold/primary.json').symlink_to(external)
    with pytest.raises(ValueError):module.recheck(root,tmp_path/'result',pin)


def test_historical_source_directory_cannot_be_used_as_output(archive):
    root,_,seal=archive
    with pytest.raises(ValueError,match='outside'):module.recheck(root,root/'accidental-mutation',seal())
    assert not (root/'accidental-mutation').exists()


def test_new_comparison_failure_cannot_inherit_old_delivery_success(archive,tmp_path,monkeypatch):
    root,_,seal=archive
    monkeypatch.setattr(module,'compare',lambda *args:dict(passed=False,native_UEL_reset_checks=[],reset_failures=[]))
    r=module.recheck(root,tmp_path/'result',seal())
    assert not r['passed'] and not r['all_registered_trajectories_agree']


def test_inputs_modified_during_audit_leave_only_incomplete_receipt(archive,tmp_path,monkeypatch):
    root,_,seal=archive;pin=seal();calls=[]
    def compare(*args):
        calls.append(1)
        if len(calls)==24:(root/'network/hold/primary.json').write_text('{}')
        return dict(passed=True,native_UEL_reset_checks=[],reset_failures=[])
    monkeypatch.setattr(module,'compare',compare)
    with pytest.raises(ValueError,match='changed during'):module.recheck(root,tmp_path/'result',pin)
    receipt=json.loads((tmp_path/'result/summary.json').read_text())
    assert not receipt['passed'] and receipt['status']=='running'


@pytest.mark.parametrize('pin',['','ABCDEF'*10,'z'*64])
def test_explicit_hash_pin_is_required(archive,tmp_path,pin):
    root,_,seal=archive;seal()
    with pytest.raises(ValueError,match='SHA256'):module.recheck(root,tmp_path/'result',pin)
    assert not (tmp_path/'result').exists()


def test_producer_change_during_recheck_does_not_publish_success(archive,tmp_path,monkeypatch):
    from benchmarks import native_online_validation
    root,_,seal=archive;calls=[]
    def identity():
        calls.append(1);return str(len(calls))
    monkeypatch.setattr(native_online_validation,'source_identity',identity)
    with pytest.raises(ValueError,match='producer changed'):module.recheck(root,tmp_path/'result',seal())
    assert not json.loads((tmp_path/'result/summary.json').read_text())['passed']


def test_existing_output_is_not_overwritten(archive,tmp_path):
    root,_,seal=archive;out=tmp_path/'result';out.mkdir();marker=out/'summary.json';marker.write_text('retain me')
    with pytest.raises(FileExistsError):module.recheck(root,out,seal())
    assert marker.read_text()=='retain me'
