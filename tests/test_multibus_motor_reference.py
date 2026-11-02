"""Multivoltage winding/MNA invariants and independent dynamic comparisons."""
import math
import numpy as np
import pytest
from benchmarks.multibus_motor_cases import example,NAMES
from benchmarks.multibus_si_motor import Circuit


@pytest.mark.parametrize('name',NAMES)
def test_si_multibus_matches_primary_controls_and_winding_physics(name):
    from power_grid_env.study_spec import run_study
    from benchmarks.multibus_motor_validation import compare_si
    spec=example(name);production=run_study(spec,isolate=True,wall_timeout_s=90.)
    assert production['completed'],production
    independent=Circuit(spec).run(production)
    assert compare_si(spec,production,independent)['passed']
    if name=='runup3s':assert all(independent['channels'][m.id+'.speed_pu'][-1]>.95 for m in spec.induction.motors)


def test_winding_oracle_uses_no_primary_network_constants_or_transformer_stamps(monkeypatch):
    spec=example('clock_tap')
    def forbidden(*args,**kwargs):raise AssertionError('primary numerical helper called')
    monkeypatch.setattr('power_grid_env.induction_network.InductionNetwork',forbidden)
    monkeypatch.setattr('power_grid_env.rms_study.transformer_parameters',forbidden)
    monkeypatch.setattr('power_grid_env.induction.InductionMotor.constants',forbidden)
    circuit=Circuit(spec)
    assert np.isfinite(circuit.rhs(0.,circuit.state)).all()


@pytest.mark.parametrize('group,connections,clock',[('YNyn0',('wye','wye'),0),('Dyn11',('delta','wye'),11),('Dyn5',('delta','wye'),5),('YNd1',('wye','delta'),1),('YNd5',('wye','delta'),5)])
def test_no_load_complex_voltage_ratio_including_clock_and_both_taps(group,connections,clock):
    spec=example('radial');spec.network.loads=[];t=spec.network.transformers[1];t.vector_group=group
    for w,connection in zip(t.windings,connections):w.connection=connection
    t.windings[0].tap_pu=1.025;t.windings[1].tap_pu=.975
    circuit=Circuit(spec);circuit.cfg['connected'][:]=False;circuit.rebuild();voltage=circuit.electrical(circuit.state)[0]
    h,l=t.windings;ratio=l.nominal_ll_v*l.tap_pu/(h.nominal_ll_v*h.tap_pu)*np.exp(-1j*clock*math.pi/6)
    actual=voltage[circuit.nodes['lv_a']]/voltage[circuit.nodes['section_a']]
    assert actual==pytest.approx(ratio,rel=1e-11,abs=1e-13)


def test_transformer_branch_conserves_power_and_dissipates_positive_loss():
    c=Circuit(example('clock_tap'))
    for name,a,b,z,ratio in c.branches:
        if not name.startswith('t_'):continue
        vh=complex(7000.,100.);vl=ratio*complex(6900.,-50.)
        ih=(vh-vl/ratio)/z;il=-ih/np.conj(ratio)
        terminal=3*(vh*np.conj(ih)+vl*np.conj(il))
        assert terminal==pytest.approx(3*z*abs(ih)**2,abs=1e-7)
        assert terminal.real>0


@pytest.mark.parametrize('fault',['charging','asymmetric_line','background_pq','magnetization','three_windings','zero_tap','connections','island','initial_latch','wrong_motor_base','bad_sensor','unknown_event','bad_source'])
def test_unsupported_multibus_scope_is_rejected_before_integration(fault):
    spec=example('thermal');n=spec.network;s=spec.induction
    if fault=='charging':n.lines[0].b_siemens[0][0]=1e-6
    elif fault=='asymmetric_line':n.lines[0].r_ohm[1][1]+=.1
    elif fault=='background_pq':n.loads[0].model='constant_pq'
    elif fault=='magnetization':n.transformers[0].imag_pct=.1
    elif fault=='three_windings':n.transformers[0].windings.append(n.transformers[0].windings[-1])
    elif fault=='zero_tap':n.transformers[0].windings[0].tap_pu=0.
    elif fault=='connections':n.transformers[1].windings[0].connection='wye'
    elif fault=='island':n.connectivity_nodes.append(n.connectivity_nodes[0].model_copy(update={'id':'orphan'}))
    elif fault=='initial_latch':s.motors[0].thermal.initial_level=s.motors[0].thermal.trip_level
    elif fault=='wrong_motor_base':s.motors[0].rated_ll_v=690.
    elif fault=='bad_sensor':s.motors[0].contactor.sense_node_id='missing'
    elif fault=='unknown_event':s.events[0].equipment_id='missing'
    else:n.sources[0].x_ohm=float('nan')
    with pytest.raises(ValueError):Circuit(spec)


@pytest.mark.parametrize('step',[0.,float('nan'),.1])
def test_native_budget_rejects_before_process_launch(step,monkeypatch):
    from benchmarks.andes_multibus_motor import native_reference
    monkeypatch.setattr('benchmarks.andes_multibus_motor.subprocess.Popen',lambda *a,**kw:pytest.fail('worker launched'))
    with pytest.raises(ValueError):native_reference(example('radial'),step)


def test_native_output_budget_rejects_before_process_launch(monkeypatch):
    from benchmarks.andes_multibus_motor import native_reference
    spec=example('radial');spec.induction.step_s=1e-12
    monkeypatch.setattr('benchmarks.andes_multibus_motor.subprocess.Popen',lambda *a,**kw:pytest.fail('worker launched'))
    with pytest.raises(ValueError,match='output budget'):native_reference(spec,.00025)


@pytest.mark.parametrize('name',['clock_tap','mixed_sensing'])
def test_native_multivoltage_initialization_and_control_do_not_read_SI_or_primary(name,monkeypatch):
    import sys,tempfile
    from pathlib import Path
    from power_grid_env.andes_rms import ensure_andes_importable
    from benchmarks.andes_multibus_motor import worker
    spec=example(name);circuit=Circuit(spec);wanted=circuit.electrical(circuit.state)[0].copy()
    assert ensure_andes_importable()
    import andes
    cache=Path(tempfile.gettempdir())/'power-grid-env-andes-pycode'
    if not (cache/'Motor5.py').is_file():andes.System(no_output=True,default_config=True,pycode_path=str(cache)).prepare(quick=True,nomp=True)
    def clear():
        for key in list(sys.modules):
            if key=='pycode' or key.startswith('pycode.'):sys.modules.pop(key,None)
    def forbidden(*args,**kwargs):raise AssertionError('native used another physical trajectory')
    monkeypatch.setattr(Circuit,'electrical',forbidden);monkeypatch.setattr(Circuit,'run',forbidden)
    monkeypatch.setattr('power_grid_env.study_spec.run_study',forbidden)
    monkeypatch.setattr('power_grid_env.induction_network.InductionNetwork',forbidden)
    clear()
    try:result=worker(spec,.00025)
    finally:clear()
    assert result['completed'] and result['stock_generated_cache_unchanged']
    assert result['explicit_source_terminal'] and not result['controller_commands_precomputed']
    assert result['native_solver_config']['reset_tiny']==0
    for node,i in circuit.nodes.items():
        data=result['node_voltage'][node];actual=complex(data['real_v'][0],data['imag_v'][0])
        assert abs(actual-wanted[i])/circuit.base[i]<1e-8
    assert result['maximum_accepted_algebraic_residual_pu']<1e-8
    for motor in spec.induction.motors:assert result['channels'][motor.id+'.speed_pu'][0]==0.
