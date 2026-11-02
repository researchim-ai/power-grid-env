"""Independent loaded equilibria, PV/controller consistency and cold regression."""
import copy
from pathlib import Path
import numpy as np
import pytest
from benchmarks.multibus_loaded_cases import example,NAMES
from benchmarks.multibus_operating_point import independent_circuit,validate_scope,CoupledCircuit
from benchmarks.multibus_si_motor import Circuit


@pytest.mark.parametrize('name',NAMES)
def test_loaded_SI_matches_primary_physics_and_controller_decisions(name):
    from power_grid_env.study_spec import run_study
    from benchmarks.multibus_motor_validation import compare_si
    spec=example(name);r=run_study(spec,isolate=True,wall_timeout_s=90.)
    assert r['completed'],r
    c=independent_circuit(spec);i=c.run(r)
    assert compare_si(spec,r,i)['passed']


@pytest.mark.parametrize('name',['grid_hold','gen_hold','gen_lv_clock'])
def test_loaded_initial_state_satisfies_all_nonthermal_differential_equations(name):
    c=independent_circuit(example(name));rhs=c.rhs(0.,c.state)
    assert max(abs(rhs))<1e-8
    assert all(.8<c.state[p['speed']]<1. for p in c.p)
    if c.generator:
        assert c.initialization['maximum_generator_initial_rhs']<1e-8
        assert abs(c.generator.initial_q_var)>1.


def test_equilibrium_thermal_policy_uses_actual_local_motor_current():
    c=independent_circuit(example('grid_warm_thermal'));currents=c.electrical(c.state)[1]
    for i,at in c.thermal.items():
        assert c.state[at]==pytest.approx((abs(currents[i])/c.p[i]['ibase'])**2,abs=1e-10)
        assert abs(c.rhs(0.,c.state)[at])<1e-8


def test_passport_thermal_transient_does_not_destroy_mechanical_equilibrium():
    spec=example('grid_warm_thermal');spec.induction.operating_point.thermal_state='passport'
    c=independent_circuit(spec);rhs=c.rhs(0.,c.state)
    assert max(abs(rhs[:c.p[-1]['speed']+1]))<1e-8
    assert rhs[c.thermal[0]]>0.


def test_independent_equilibrium_avoids_primary_initialization_and_stamps(monkeypatch):
    spec=example('gen_lv_clock')
    def forbidden(*args,**kwargs):raise AssertionError('primary equilibrium helper called')
    for path in ['power_grid_env.induction_network.InductionNetwork','power_grid_env.motor_operating_point.initialize_motor_state','power_grid_env.coupled_initialization.initialize_coupled','power_grid_env.rms_study.transformer_parameters','power_grid_env.induction.InductionMotor.constants']:
        monkeypatch.setattr(path,forbidden)
    assert independent_circuit(spec).initialization['maximum_joint_initial_rhs']<1e-8


def test_native_scope_does_not_compute_SI_equilibrium(monkeypatch):
    spec=example('gen_hold')
    def forbidden(*args,**kwargs):raise AssertionError('SI operating point computed on native path')
    for method in ['steady_response','initialize_equilibrium','electrical','rhs']:monkeypatch.setattr(Circuit,method,forbidden)
    monkeypatch.setattr(CoupledCircuit,'initialize_joint',forbidden)
    assert validate_scope(spec).generator is None


@pytest.mark.parametrize('fault',['anisotropic','governor','exciter','voltage','phases','mode','event'])
def test_unqualified_coupled_scope_rejects(fault):
    spec=example('gen_hold');p=spec.coupled.machines[0];g=spec.network.generators[0]
    if fault=='anisotropic':p.x_q_subtransient_pu=.2
    elif fault=='governor':p.governor=None
    elif fault=='exciter':p.exciter=None
    elif fault=='voltage':g.ll_v=400.
    elif fault=='phases':g.phases=['A','B']
    elif fault=='mode':g.mode='pq'
    else:
        from power_grid_env.coupled_rms import CoupledEvent
        spec.coupled.events=[CoupledEvent(time_s=.2,kind='generator_open',equipment_id=g.id)]
    with pytest.raises(ValueError):validate_scope(spec)


@pytest.mark.parametrize('name,match',[('generator_q_limit','initial Q limit'),('cold_generator_q_limit','initial Q limit'),('motor_high_speed_root','high-speed')])
def test_independent_expected_initialization_rejections(name,match):
    with pytest.raises(ValueError,match=match):independent_circuit(example(name))


def test_stationary_check_catches_spurious_motion_below_dynamic_error_limit():
    from benchmarks.multibus_loaded_validation import stationary_check,channel_limits
    spec=example('gen_hold');channels={key:[0.,0.] for key in channel_limits(spec)}
    assert stationary_check(spec,{'x':dict(channels=channels)})['passed']
    channels['m.speed_pu'][1]=1e-7
    assert not stationary_check(spec,{'x':dict(channels=channels)})['passed']


def test_incomplete_cold_native_run_is_failure_without_reading_absent_channels():
    from benchmarks.multibus_loaded_validation import runup_check
    settings=example('gen_cold_start').coupled
    result=runup_check(settings,{}, {},dict(completed=False,status='native_timeout'))
    assert not result['runup_passed'] and result['runup_failure']=='incomplete_native_run'


def test_loaded_protocol_precedes_native_integration(tmp_path,monkeypatch):
    import json
    from benchmarks import multibus_loaded_validation as validation
    def stop(*args,**kwargs):
        protocol=json.loads((tmp_path/'protocol.json').read_text())
        assert protocol['initial_motor_and_generator_rhs_limit']==1e-8
        assert 'grid_hold' in protocol['stationary_channel_drift_limits']
        raise RuntimeError('preflight stop')
    monkeypatch.setattr(validation,'native_reference',stop)
    with pytest.raises(RuntimeError,match='preflight stop'):validation.run(tmp_path,['grid_hold'])


def test_native_loaded_generator_initialization_reads_no_other_solution(monkeypatch):
    import sys,tempfile
    from power_grid_env.andes_rms import ensure_andes_importable
    from benchmarks.andes_multibus_motor import worker
    from benchmarks.group_si_generator import Generator
    spec=example('gen_lv_clock');expected=independent_circuit(spec).initialization
    assert ensure_andes_importable()
    import andes
    stock=Path(tempfile.gettempdir())/'power-grid-env-andes-pycode'
    if not (stock/'Motor5.py').is_file():andes.System(no_output=True,default_config=True,pycode_path=str(stock)).prepare(quick=True,nomp=True)
    def clear():
        for key in list(sys.modules):
            if key=='pycode' or key.startswith('pycode.'):sys.modules.pop(key,None)
    def forbidden(*args,**kwargs):raise AssertionError('another solution used for native initialization')
    for method in ['steady_response','initialize_equilibrium','electrical','rhs']:monkeypatch.setattr(Circuit,method,forbidden)
    monkeypatch.setattr(CoupledCircuit,'initialize_joint',forbidden);monkeypatch.setattr(Generator,'at_terminal',forbidden)
    monkeypatch.setattr('power_grid_env.study_spec.run_study',forbidden)
    clear()
    try:r=worker(spec,.00025)
    finally:clear()
    assert r['completed'] and r['stock_generated_cache_unchanged']
    init=r['initialization'];assert init['maximum_motor_initial_rhs']<1e-8 and init['maximum_generator_initial_rhs']<1e-8
    assert init['generator_initial_q_var']==pytest.approx(expected['generator_initial_q_var'],abs=.01)
    for motor in spec.coupled.motors:assert r['channels'][motor.id+'.speed_pu'][0]>.8
