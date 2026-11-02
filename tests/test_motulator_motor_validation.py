"""Published motor referral, units, signs and independent engine provenance."""
import math,os,sys
from pathlib import Path
import numpy as np
import pytest
from benchmarks import motulator_motor_validation as v


@pytest.mark.parametrize('speed',[0.,.8,1.,1.03,1.2])
def test_native_primary_equilibrium_matches_analytic_power_and_torque_across_slip(speed):
    a=v.analytic(50.,1.,speed,.95);p=v.primary(50.,1.,speed,.95,5000.)
    for key in v.LIMITS:assert abs(a[key]-p[key])<v.LIMITS[key]
    assert math.copysign(1,a['torque_nm'])==math.copysign(1,1-speed) if speed!=1 else abs(a['torque_nm'])<1e-12
    if speed>1:assert a['p_w']<0
    assert a['q_var']>0


@pytest.mark.parametrize('speed',[0.,.97,1.03])
def test_rotor_referral_and_network_power_base_do_not_change_physical_ports(speed):
    a=v.primary(60.,.8,speed,.95,5000.);b=v.primary(60.,.8,speed,.98,10000.)
    for key in v.LIMITS:assert abs(a[key]-b[key])<v.LIMITS[key]


@pytest.mark.parametrize('beta',[0.,.9,1.,float('nan')])
def test_nonphysical_T_referral_rejected(beta):
    with pytest.raises(ValueError):v.t_parameters(beta)


def test_steady_passivity_and_three_phase_real_power_balance():
    p=v.t_parameters(.95);a=v.analytic(50.,1.,0.,.95);i=a['current_a'];terminal=a['voltage_v']
    source=400/math.sqrt(3)
    assert 3*(source*np.conj(i)).real-a['p_w']==pytest.approx(3*v.Z_EXTERNAL.real*abs(i)**2,abs=1e-10)
    assert a['p_w']>3*p['rs']*abs(i)**2


@pytest.fixture
def external_dependency(monkeypatch):
    dep=os.environ.get('PGE_MOTULATOR_REFERENCE_DIR')
    if dep:monkeypatch.syspath_prepend(dep)
    pytest.importorskip('motulator',reason='optional pinned external reference; enabled in dedicated CI job')


@pytest.mark.parametrize('frequency,speed',[(50.,0.),(50.,1.),(50.,1.03),(60.,.8)])
def test_external_engine_uses_no_primary_or_analytic_solution(external_dependency,monkeypatch,frequency,speed):
    expected=v.analytic(frequency,1.,speed,.95)
    def forbidden(*args,**kwargs):raise AssertionError('primary or analytic solution leaked into external engine')
    monkeypatch.setattr(v,'analytic',forbidden);monkeypatch.setattr(v,'primary',forbidden)
    monkeypatch.setattr('power_grid_env.induction_network.InductionNetwork',forbidden)
    result,error=v.external(frequency,1.,speed)
    assert error<1e-9
    for key in v.LIMITS:assert abs(result[key]-expected[key])<v.LIMITS[key]


def test_wrong_external_version_fails_explicitly(external_dependency,monkeypatch):
    monkeypatch.setattr('importlib.metadata.version',lambda name:'0.18.0')
    with pytest.raises(ValueError,match='pinned'):v.external_machine()


def test_wrong_wheel_hash_rejected_before_primary_runs(external_dependency,tmp_path,monkeypatch):
    wheel=tmp_path/'bad.whl';wheel.write_bytes(b'not the published package')
    monkeypatch.setattr(v,'primary',lambda *args:pytest.fail('primary ran before wheel verification'))
    with pytest.raises(ValueError,match='wheel hash'):v.run(tmp_path/'results',wheel)


def test_protocol_is_written_before_first_motor_solve(external_dependency,tmp_path,monkeypatch):
    import json
    wheel=os.environ.get('PGE_MOTULATOR_WHEEL')
    if not wheel:pytest.skip('pinned wheel path is supplied by dedicated reference job')
    def stop(*args):
        p=json.loads((tmp_path/'protocol.json').read_text())
        assert p['native_steady_RHS_limit']==1e-9 and p['wheel_sha256']==v.WHEEL_SHA256
        assert p['full_stator_transient_comparison'] is False
        raise RuntimeError('protocol checked')
    monkeypatch.setattr(v,'primary',stop)
    with pytest.raises(RuntimeError,match='protocol checked'):v.run(tmp_path,Path(wheel))
    assert json.loads((tmp_path/'summary.json').read_text())['passed'] is False
    assert json.loads((tmp_path/'summary.json').read_text())['status']=='failed'


def test_installed_reference_source_tampering_is_rejected(external_dependency,tmp_path,monkeypatch):
    wheel=os.environ.get('PGE_MOTULATOR_WHEEL')
    if not wheel:pytest.skip('pinned wheel required by dedicated reference CI')
    original=v.sha256_file
    def changed(path):
        if 'motulator' in Path(path).parts and Path(path).suffix=='.py':return '0'*64
        return original(path)
    monkeypatch.setattr(v,'sha256_file',changed)
    with pytest.raises(ValueError,match='installed native source'):v.run(tmp_path,Path(wheel))


def test_open_source_release_policy_does_not_require_private_hardware_or_measurements():
    import yaml
    root=Path(__file__).resolve().parents[1]
    p=yaml.safe_load((root/'plans/open-reference-validation.yaml').read_text())
    assert p['own_hardware_required'] is False
    assert p['user_site_data_required_for_open_source_release'] is False
    assert p['validation_tracks']['cross_simulator']['required'] is True
    assert p['validation_tracks']['published_experiments']['required_for_open_source_release'] is False
    assert not any('private' in gate or 'own_hardware' in gate for gate in p['release_gates'])
