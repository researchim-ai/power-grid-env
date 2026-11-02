"""Executable Modelica equations and independence of the native closed loop."""
from pathlib import Path
import json,os
import numpy as np
import pytest
from benchmarks.oel2c_native_reference import NAMES,example,primary,native,compare,CHANNELS
from benchmarks import dynawo_reference_setup as setup


@pytest.mark.parametrize('name',NAMES)
def test_compiled_dassl_uses_its_own_clocks_and_matches_all_block_states(name,tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned Dynawo/Modelica/FMI reference not configured')
    setup.verify(root);case=example(name)
    reference=native(case,tmp_path/'native',root,1e-12)
    result=compare(primary(case),reference)
    assert result['passed'],result
    assert not reference['original_Dynawo_equations']
    assert 'cap guard v1' in reference['reference_variant']


def test_model_exchange_feedback_does_not_call_the_production_limiter(monkeypatch,tmp_path):
    root=os.environ.get('PGE_DYNAWO_REFERENCE_ROOT')
    if not root:pytest.skip('optional pinned Dynawo/Modelica/FMI reference not configured')
    from benchmarks.generator_oel2c_cases import example as network
    from benchmarks.oel2c_model_exchange import run
    from power_grid_env.generator_oel2c import OEL2CDynamics
    def forbidden(*args,**kwargs):raise AssertionError('production limiter called by native reference')
    for method in ['initial','rhs','quantities','apply','signals','guards']:
        monkeypatch.setattr(OEL2CDynamics,method,forbidden)
    spec=network('voltage_step');spec.coupled.duration_s=.4
    r=run(spec,tmp_path/'independent',root,1e-10)
    assert r['completed'] and r['native_OEL2C_in_feedback'] and r['independent_SI_network']
    assert r['time_s'][-1]==.4 and not r['production_limiter_RHS_used']
    assert min(r['channels']['g2.OEL2C.output_pu'])<0


def test_patch_is_explicit_local_and_leaves_original_bytes_unchanged():
    source=b'  //Input variables\n  elseif IRefPu == IInstPu and timer1.y > tOff then\n'
    original=source[:];changed=setup.guarded_bytes(setup.PATCH_FILE,source)
    assert source==original and b'ICapTolPu=1e-8' in changed
    assert b'IRefPu >= IInstPu - ICapTolPu' in changed
    assert setup.guarded_bytes('other.mo',source)==original
    with pytest.raises(ValueError):setup.guarded_bytes(setup.PATCH_FILE,b'no expected source')


def test_changed_guarded_reference_is_rejected(tmp_path):
    lock=json.loads(setup.LOCK.read_text())
    for name in lock['files']:
        p=tmp_path/'dynawo-source'/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(b'  //Input variables\nIRefPu == IInstPu' if name==setup.PATCH_FILE else b'original')
    setup.install_guarded_copy(tmp_path);setup.verify_guarded_copy(tmp_path)
    changed=tmp_path/'dynawo-cap-guard-v1'/setup.PATCH_FILE
    changed.write_bytes(changed.read_bytes()+b'\nchanged')
    with pytest.raises(ValueError,match='changed numerical-guard'):setup.verify_guarded_copy(tmp_path)


def test_changed_archive_fails_before_extract_or_compile(tmp_path,monkeypatch):
    monkeypatch.setattr(setup,'verify_base',lambda root:None)
    item=json.loads(setup.LOCK.read_text())['MSL'];p=tmp_path/'packages'/item['filename'];p.parent.mkdir();p.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed Dynawo reference archive'):setup.prepare(tmp_path)


def test_missing_reference_never_downloads_without_explicit_fetch(tmp_path,monkeypatch):
    monkeypatch.setattr(setup,'verify_base',lambda root:None)
    with pytest.raises(ValueError,match='missing optional archive'):setup.prepare(tmp_path)


def test_missing_grid_or_corrupt_internal_state_cannot_hide_behind_zero_output():
    a=dict(time_s=[0.,.5,1.],channels={k:[0.,0.,0.] for k in CHANNELS})
    b=dict(time_s=[0.,1.],channels={k:[0.,0.] for k in CHANNELS})
    with pytest.raises(ValueError,match='native query grid sample missing'):compare(a,b)
    b=dict(time_s=[0.,.5,1.],channels={k:[0.,0.,0.] for k in CHANNELS})
    assert compare(a,b)['passed']
    b['channels']['pid_integral_pu'][1]=.01
    assert not compare(a,b)['passed']


def test_joint_cold_reference_rejects_equilibrium_UEL_without_substitution():
    from benchmarks.generator_oel2c_cases import example as network
    from benchmarks.oel2c_model_exchange import scope
    s=network('UEL_coordination');s.coupled.underexcitation_limiters[0].initialization='inactive_equilibrium'
    with pytest.raises(ValueError,match='zero_output_startup'):scope(s)


def test_initial_grid_point_does_not_consume_a_later_native_event():
    a=dict(time_s=[0.,.5],channels={k:[0.,0.] for k in CHANNELS})
    b=dict(time_s=[0.,1e-16,.5],channels={k:[0.,0.,0.] for k in CHANNELS})
    b['channels']['enable_timer_active'][1]=1.
    b['channels']['enable_timer_start_s'][1]=1e-16
    assert compare(a,b)['passed']
