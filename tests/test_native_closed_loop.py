"""Independent online controls: thermal integration, rollback and native events."""
from types import SimpleNamespace as NS
import math

import numpy as np
import pytest
from scipy.integrate import quad

from benchmarks.native_checkpoint import NativeCheckpoint
from benchmarks.native_closed_driver import thermal_step


@pytest.fixture
def stock_native_cache():
    """Allow these integration tests to run alone on a fresh checkout."""
    import tempfile
    import sys
    from pathlib import Path
    from power_grid_env.andes_rms import ensure_andes_importable
    assert ensure_andes_importable(), 'ANDES extra required'
    import andes
    cache=Path(tempfile.gettempdir())/'power-grid-env-andes-pycode'
    if not all((cache/(name+'.py')).is_file() for name in ('Motor3','Motor5','GENROU','TGOV1','SEXS')):
        system=andes.System(no_output=True,default_config=True,pycode_path=str(cache))
        system.prepare(quick=True,nomp=True)
    # Workers normally live in separate processes. Direct instrumentation in
    # these tests must not retain imports of an already removed private cache.
    def clear_private_imports():
        for key in list(sys.modules):
            if key=='pycode' or key.startswith('pycode.'):sys.modules.pop(key,None)
    clear_private_imports()
    try:yield
    finally:clear_private_imports()


@pytest.mark.parametrize('duration,tau',[(1e-12,10.),(.001,10.),(1.,2.),(100.,.1)])
def test_thermal_ramp_matches_independent_integral(duration,tau):
    initial,h0,h1=.3,.5,3.
    # Integrate in decay-time coordinates to resolve long intervals reliably.
    q=duration/tau
    integral=quad(lambda u: math.exp(-u)*(h1+(h0-h1)*u/q),0,min(q,750.),epsabs=1e-12)[0]
    assert thermal_step(initial,h0,h1,duration,tau)==pytest.approx(initial*math.exp(-q)+integral,abs=2e-12)


def test_linear_heat_is_invariant_under_interval_partition():
    whole=thermal_step(.2,1.,4.,.7,2.)
    first=thermal_step(.2,1.,2.,.7/3,2.)
    assert thermal_step(first,2.,4.,.7*2/3,2.)==pytest.approx(whole,abs=1e-14)
    assert thermal_step(.2,1.,4.,0.,2.)==.2


@pytest.mark.parametrize('index,value',[(i,v) for i in range(5) for v in (float('nan'),float('inf'),-float('inf'))])
def test_nonfinite_thermal_state_cannot_enter_native_trajectory(index,value):
    args=[.2,1.,2.,.1,2.];args[index]=value
    with pytest.raises(ValueError,match='finite'):thermal_step(*args)


def checkpoint_system():
    block=type('AntiWindup',(),{})()
    block.zu=np.array([0.]);block.x_set=[(1,2.)]
    service=NS(v=np.array([3.]))
    model=NS(n=1,services={'s':service},services_post={},num_params={},discrete={'limit':block})
    system=NS(dae=NS(**{k:np.array([0.]) for k in ('x','y','f','g','b','t')}),
              TDS=NS(config=NS(honest=1),method=type('Trapezoid',(),{})(),
                     h=.01,solver=NS(worker=NS(factorize=False))),
              exist=NS(pflow_tds={'Motor3':model}),vars_to_models=lambda:None)
    return system,block,service


def test_rollback_restores_limiter_memory_services_and_original_array_bindings():
    system,block,service=checkpoint_system();checkpoint=NativeCheckpoint(system)
    original_x=system.dae.x;original_service=service.v;expected=checkpoint.expected_digest
    system.dae.x=np.array([42.]);service.v[:]=8.;block.zu[:]=1.;block.x_set.append((2,9.));system.TDS.h=.5
    assert checkpoint.digest()!=expected
    checkpoint.restore()
    assert checkpoint.digest()==expected and checkpoint.arrays_keep_identity()
    assert system.dae.x is original_x and service.v is original_service
    assert block.x_set==[(1,2.)] and block.zu.tolist()==[0.]
    assert system.TDS.solver.worker.factorize
    system.dae.x[:]=7.;checkpoint.capture();system.dae.x[:]=9.;checkpoint.restore()
    assert system.dae.x.tolist()==[7.]


def test_repeated_capture_keeps_saved_buffers_detached_and_updates_full_state():
    system,block,service=checkpoint_system();checkpoint=NativeCheckpoint(system)
    for step in range(4):
        system.dae.x[:]=step;service.v[:]=step+2.;block.x_set=[(step,float(step))]
        checkpoint.capture();before=checkpoint.expected_digest
        system.dae.x[:]=99.;service.v[:]=88.;block.x_set=[]
        checkpoint.restore()
        assert checkpoint.digest()==before and checkpoint.arrays_keep_identity()
        assert system.dae.x.tolist()==[float(step)] and service.v.tolist()==[step+2.]
        assert block.x_set==[(step,float(step))]


def test_failed_capture_cannot_restore_a_partially_overwritten_checkpoint():
    system,_,service=checkpoint_system();checkpoint=NativeCheckpoint(system)
    system.dae.x[:]=7.;service.v=object()
    with pytest.raises(ValueError,match='changed type'):checkpoint.capture()
    with pytest.raises(ValueError,match='incomplete'):checkpoint.restore()


@pytest.mark.parametrize('unsupported',['model','discrete','method','dishonest'])
def test_unaudited_native_rollback_scope_is_rejected(unsupported):
    system,block,_=checkpoint_system()
    if unsupported=='model':system.exist.pflow_tds['Unknown']=system.exist.pflow_tds.pop('Motor3')
    elif unsupported=='discrete':system.exist.pflow_tds['Motor3'].discrete['limit']=NS()
    elif unsupported=='method':system.TDS.method=NS()
    else:system.TDS.config.honest=0
    with pytest.raises(ValueError):NativeCheckpoint(system)


@pytest.mark.parametrize('mutate',[False,True])
def test_failed_native_driver_still_audits_stock_cache(tmp_path,monkeypatch,mutate):
    from benchmarks import andes_group_motor_reference as adapter
    from benchmarks.automatic_motor_group import example
    stock=tmp_path/'power-grid-env-andes-pycode';stock.mkdir()
    for name in ('Motor3.py','Motor5.py'):(stock/name).write_text('# stock\n')
    monkeypatch.setattr(adapter.tempfile,'gettempdir',lambda:str(tmp_path))
    monkeypatch.setattr(adapter,'scope',lambda spec:None)
    def failed(*args,**kwargs):
        if mutate:(stock/'Motor3.py').write_text('# changed\n')
        raise RuntimeError('trial failed')
    monkeypatch.setattr(adapter,'native_run',failed)
    expected=ValueError if mutate else RuntimeError
    with pytest.raises(expected,match='stock code cache changed' if mutate else 'trial failed'):
        adapter.worker(example('grid_staggered'),.00025)


def test_native_online_feedback_generates_its_own_event_times(monkeypatch,stock_native_cache):
    from benchmarks.automatic_motor_group import example
    from benchmarks.native_closed_loop import execute
    def forbidden(*args,**kwargs):raise AssertionError('production decisions used')
    monkeypatch.setattr('power_grid_env.study_spec.run_study',forbidden)
    monkeypatch.setattr('power_grid_env.motor_contactor.MotorContactorBank',forbidden)
    monkeypatch.setattr('benchmarks.automatic_group_native.command_oracle',forbidden)
    result=execute(example('generator_feedback'),.000125)
    assert result['completed'],result
    assert not result['controller_commands_precomputed']
    assert result['stock_generated_cache_unchanged']
    assert max(result['online_sensor_observer_errors_pu'].values())<1e-6
    metadata=result['native_driver_metadata']
    assert metadata['rollback_count']>0
    events=metadata['event_log']
    initial_unavailable=[e for e in events if e['kind']=='contactor_voltage_unavailable' and e['time_s']<=.5]
    assert len(initial_unavailable)==3 and all(e['time_s']==.2 for e in initial_unavailable)
    unavailable=[e for e in events if e['kind']=='contactor_voltage_unavailable' and e['time_s']>.5]
    assert {e['equipment_id'] for e in unavailable}=={'m','m2','m3'}
    # Independent SI reference crossing; this is a tolerance, never an input
    # command to the native driver.
    assert all(abs(e['time_s']-.6581573474891915)<1e-6 for e in unavailable)
    restarts=[e for e in events if e.get('cause')=='automatic_restart']
    assert [e['equipment_id'] for e in restarts]==['m','m2','m3']
    assert [e['time_s'] for e in restarts]==pytest.approx([.62,.9881573474891915,1.1381573474891915],abs=1e-6)
    assert result['maximum_accepted_algebraic_residual_pu']<=1e-8
    assert not result['field_validated'] and not result['stock_native_controller_qualified']


@pytest.mark.parametrize('case',['grid_thermal_tie','grid_thermal_near'])
def test_online_native_thermal_trips_preserve_exact_and_near_ties(case,stock_native_cache):
    from benchmarks.automatic_motor_group import example
    from benchmarks.native_closed_loop import native_reference
    result=native_reference(example(case),.000125)
    assert result['completed'],result
    trips={e['equipment_id']:e['time_s'] for e in result['native_driver_metadata']['event_log'] if e['kind']=='thermal_trip'}
    assert set(trips)=={'m','m2'}
    assert trips['m']==pytest.approx(.010727744644482026,abs=1e-6)
    if case.endswith('_tie'):assert trips['m']==trips['m2']
    else:assert 0<trips['m2']-trips['m']<1e-8
    assert result['maximum_accepted_algebraic_residual_pu']<=1e-8


def test_real_native_limiter_trial_replays_after_complete_rollback(tmp_path,stock_native_cache):
    from benchmarks.native_checkpoint_probe import probe
    result=probe('generator_saturated',tmp_path/'probe.json')
    assert result['passed'] and result['complete_restore_verified']
    assert result['maximum_replay_error']<=1e-10
    assert any(1. in flags for flags in result['limiter_upper_flags_after_discarded_trial'].values())
