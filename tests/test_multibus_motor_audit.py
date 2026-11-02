"""Cross-case checks must see changed physics, phase and damaged evidence."""
import copy,math
import numpy as np
import pytest
from benchmarks.multibus_motor_cases import example
from benchmarks.multibus_motor_audit import base_error,initial_port_effect,check_files
from benchmarks.native_online_validation import channel_limits
from benchmarks.andes_multibus_motor import native_branch_input
from power_grid_env.report import sha256_file


def packet(spec):
    levels={v.id:v.nominal_ll_v/math.sqrt(3) for v in spec.network.voltage_levels}
    return dict(time_s=[0.,1.2],sample_side=['regular','regular'],channels={key:[0.,0.] for key in channel_limits(spec)},
                node_voltage={n.id:dict(real_v=[levels[n.voltage_level_id]]*2,imag_v=[0.,0.]) for n in spec.network.connectivity_nodes})


def test_identical_physics_on_different_system_bases_has_zero_error():
    a,b=example('radial'),example('base_change')
    assert base_error(a,b,packet(a),packet(b),'native_fine')==0.


def test_power_base_comparison_uses_motor_VA_not_watts():
    a,b=example('radial'),example('base_change');left,right=packet(a),packet(b)
    right['channels']['m.q_var'][1]=.001*a.induction.motors[0].rated_va
    assert base_error(a,b,left,right,'native_fine')==pytest.approx(.001)


def test_base_change_cannot_hide_changed_physical_impedance():
    a,b=example('radial'),example('base_change');b.network.sources[0].r_ohm+=1.
    with pytest.raises(ValueError,match='beyond'):base_error(a,b,packet(a),packet(b),'native_fine')


def test_clock_effect_compares_complex_voltage_even_at_equal_magnitude():
    spec=example('radial');left=packet(spec);right=copy.deepcopy(left)
    v=right['node_voltage']['lv_b']['real_v'][0];angle=5*math.pi/6
    right['node_voltage']['lv_b']=dict(real_v=[v*math.cos(angle)]*2,imag_v=[v*math.sin(angle)]*2)
    assert initial_port_effect(spec,spec,left,right,'native_fine','lv_b')==pytest.approx(2*math.sin(angle/2))


def test_evidence_manifest_detects_changed_file(tmp_path):
    path=tmp_path/'data.json';path.write_text('{}');manifest={'data.json':sha256_file(path)}
    check_files(tmp_path,manifest)
    path.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='integrity'):check_files(tmp_path,manifest)


@pytest.mark.parametrize('name',['../outside','/tmp/outside'])
def test_manifest_cannot_escape_evidence_root(tmp_path,name):
    with pytest.raises(ValueError,match='unsafe'):check_files(tmp_path,{name:'irrelevant'})


def test_manifest_rejects_symlinks(tmp_path):
    data=tmp_path/'original';data.write_text('value');(tmp_path/'link').symlink_to(data)
    with pytest.raises(ValueError,match='unsafe'):check_files(tmp_path,{'link':sha256_file(data)})


@pytest.mark.parametrize('base',[5e6,10e6,100e6])
@pytest.mark.parametrize('impedance',[1.+5j,0.+.2j,.001+.01j])
def test_native_regularization_translation_preserves_physical_ohms(base,impedance):
    zb=1e4**2/base;values=native_branch_input(impedance/zb)
    effective=complex(values['r']+1e-8,values['x']+1e-8)*zb
    assert effective==pytest.approx(impedance,rel=1e-14,abs=1e-15)


@pytest.mark.parametrize('z',[-1.+1j,1.+0j,complex(float('nan'),1.),1.+1e-8j])
def test_unrepresentable_or_nonpassive_branch_is_rejected(z):
    with pytest.raises(ValueError):native_branch_input(z)


def test_cli_corpus_registers_cross_case_protocol_before_first_worker(tmp_path,monkeypatch):
    import json
    from benchmarks import multibus_motor_validation as validation
    def first_worker(*args,**kwargs):
        assert json.loads((tmp_path/'metamorphic-protocol.json').read_text())==validation.METAMORPHIC_PROTOCOL
        assert (tmp_path/'protocol.json').is_file()
        raise RuntimeError('intentional preflight stop')
    monkeypatch.setattr(validation,'native_reference',first_worker)
    with pytest.raises(RuntimeError,match='preflight'):validation.run(tmp_path,['radial'])
    assert not json.loads((tmp_path/'summary.json').read_text())['passed']
