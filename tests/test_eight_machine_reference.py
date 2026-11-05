"""Explicit native reference scope and independent thermal/MNA invariants."""
import copy
import numpy as np
import pytest
from benchmarks.sparse_jacobian_validation import case
from benchmarks.oel2c_model_exchange import scope as oel_scope
from benchmarks.esst1a_network import scope as esst_scope
from benchmarks.eight_machine_circuit import EightMachineCircuit,PROFILE
from power_grid_env.coupled_rms import CoupledEvent


@pytest.mark.parametrize('name',['max_genrou_8_32','max_genrou_long_8_32'])
def test_eight_machine_OEL_requires_explicit_reference_profile(name):
    s=case(name)
    with pytest.raises(ValueError):oel_scope(s)
    baseline=oel_scope(s,profile=PROFILE)
    assert len(baseline.coupled.machines)==8 and not baseline.coupled.oel2c_limiters
    assert all(m.stabilizer is None for m in baseline.coupled.machines)
    assert all(m.stabilizer is not None for m in s.coupled.machines)
    assert all(m.thermal for m in baseline.coupled.motors)


def test_eight_machine_ESST_requires_explicit_reference_profile():
    s=case('max_esst_8_32')
    with pytest.raises(ValueError):esst_scope(s)
    baseline=esst_scope(s,profile=PROFILE)
    assert len(baseline.coupled.machines)==8 and not baseline.coupled.esst1a_exciters
    assert all(m.thermal for m in baseline.coupled.motors)
    assert all(m.exciter and not m.stabilizer for m in baseline.coupled.machines)


@pytest.mark.parametrize('which',['oel','esst'])
def test_unknown_profile_is_not_accepted(which):
    s=case('max_genrou_8_32' if which=='oel' else 'max_esst_8_32')
    with pytest.raises(ValueError,match='unknown'):(oel_scope if which=='oel' else esst_scope)(s,profile='unbounded')


def plant():return EightMachineCircuit(oel_scope(case('max_genrou_8_32'),profile=PROFILE))


def test_independent_sparse_root_and_all_node_balance():
    p=plant();v,st,_,_=p.electrical(p.state);audit=p.physical_balance(v,st)
    assert len(p.ports)==8 and p.M==32 and p.N==512 and p.sparse_network
    assert max(audit.values())<1e-10
    assert p.initialization['maximum_static_residual']<1e-9


def test_motor_thermal_trip_latches_preserves_flux_and_cools():
    p=plant();cfg=copy.deepcopy(p.cfg);cfg['connected'][0]=True;p.set_context(cfg)
    a=p.thermal[0];relay=p.s.motors[0].thermal;relay.trip_level=.1;p.state[a]=relay.trip_level
    saved=p.state.copy();cfg=copy.deepcopy(p.cfg);logs=p.extra_apply({('motor_thermal',0)},.2,p.state,cfg)
    assert np.array_equal(saved,p.state)
    assert any(e['kind']=='thermal_trip' and e['equipment_id']=='m0' for e in logs)
    assert cfg['latched'][0] and not cfg['connected'][0]
    p.set_context(cfg);v,st,_,_=p.electrical(p.state)
    assert abs(st[0])<1e-12 and abs(p.rhs(.2,p.state)[a]+p.state[a]/relay.time_constant_s)<1e-12
    e=CoupledEvent(time_s=.4,kind='motor_close',equipment_id='m0')
    assert p.motor_command(e,cfg)=='blocked_thermal_latch' and not cfg['connected'][0]


def test_source_change_invalidates_voltage_cache_without_refactorization():
    p=plant();v0=p.electrical(p.state)[0].copy();n=p.factorizations;cfg=copy.deepcopy(p.cfg);cfg['scale']=.9;p.set_context(cfg)
    v=p.electrical(p.state)[0]
    assert not np.array_equal(v,v0) and p.factorizations==n
    assert max(p.physical_balance(v,p.electrical(p.state)[1]).values())<1e-10


def test_generator_open_refactors_independent_network():
    p=plant();n=p.factorizations;cfg=copy.deepcopy(p.cfg);cfg['generator_connected'][0]=False;p.set_context(cfg);p.electrical(p.state)
    assert p.factorizations==n+1 and abs(p.generator_currents[0])<1e-10


def test_motor_thermal_channel_inventory_is_complete():
    p=plant();channels=p.extra_signals(p.state)
    assert all(m.id+'.thermal_level' in channels and m.id+'.thermal_latched' in channels for m in p.s.motors)


@pytest.mark.parametrize('name',['max_genrou_8_32','max_esst_8_32','max_genrou_long_8_32'])
def test_original_NA09_hashes_are_unchanged(name):
    from benchmarks.eight_machine_validation import case as declared,SOURCE_CASE_HASHES
    from power_grid_env.model import canonical_hash
    assert canonical_hash(declared(name))==SOURCE_CASE_HASHES[name]


def test_required_inventory_includes_all_control_and_cage_groups():
    from benchmarks.eight_machine_circuit import extra_required
    s=case('max_genrou_8_32');required=extra_required(s)
    assert all(m.id+'.thermal_level' in required and m.id+'.rotor2_copper_loss_w' in required for m in s.coupled.motors)
    assert all(m.generator_id+'.IEEEST.output_pu' in required and m.generator_id+'.UEL.raw_lag_state_pu' in required for m in s.coupled.machines)


def test_negative_audit_rejected_before_channel_comparison():
    from benchmarks.eight_machine_validation import compare
    b=dict(native_profile=PROFILE,time_s=[0.],network_balance=[dict(kcl_pu=-1.,power_balance_pu=0.)])
    with pytest.raises(ValueError,match='negative'):compare(case('max_genrou_8_32'),{},b)


def test_missing_audit_rejected_before_channel_comparison():
    from benchmarks.eight_machine_validation import compare
    with pytest.raises(ValueError,match='missing'):compare(case('max_genrou_8_32'),{},dict(native_profile=PROFILE))


def test_unsupported_PSS_mode_rejected_in_extended_scope():
    s=case('max_esst_8_32');s.coupled.machines[0].stabilizer.MODE=2
    with pytest.raises(ValueError,match='MODE1'):esst_scope(s,profile=PROFILE)


def test_repeated_motor_commands_keep_explicit_outcomes():
    p=plant();cfg=copy.deepcopy(p.cfg);event=CoupledEvent(time_s=.2,kind='motor_open',equipment_id='m0')
    assert p.motor_command(event,cfg)=='already_in_requested_state'
    event.kind='motor_close';assert p.motor_command(event,cfg)=='applied'
    assert p.motor_command(event,cfg)=='already_in_requested_state'


@pytest.mark.parametrize('protected',[False,True])
def test_repeated_close_scope_accounts_for_autonomous_thermal_trip(protected):
    from power_grid_env.coupled_rms import validate_coupled
    from benchmarks.eight_machine_validation import case as declared
    s=declared('motor_trip_latch')
    if not protected:s.coupled.motors[0].thermal=None
    scope=validate_coupled(s.network,s.coupled)
    assert scope['ok'] is protected,scope
    assert ('redundant contactor operation' in scope['errors']) is (not protected)


def test_thermal_reclose_without_intervening_open_is_dynamically_blocked():
    from pathlib import Path
    from power_grid_env.study_spec import StudySpec,run_study
    s=StudySpec.model_validate_json(Path('cases/studies/thermal_blocked_reclose.json').read_text())
    s.induction.events=[e for e in s.induction.events if e.kind!='motor_open']
    r=run_study(s);assert r['completed'],r
    assert any(e['kind']=='thermal_trip' for e in r['event_log'])
    assert next(e for e in r['event_log'] if e['kind']=='motor_close')['outcome']=='blocked_thermal_latch'
    assert not r['motor_summary']['m']['connected_at_end']
