"""End-to-end restart physics and fail-closed protection evidence."""
import copy
from functools import lru_cache
import math
import numpy as np
import pytest
from benchmarks.multibus_restart_cases import example,NAMES
from benchmarks.multibus_restart_audit import contract,coast_check
from benchmarks.multibus_operating_point import independent_circuit
from power_grid_env.study_spec import run_study


@pytest.fixture(scope='module')
def primary():
    @lru_cache(None)
    def get(name):
        result=run_study(example(name),isolate=True,wall_timeout_s=90.)
        assert result['completed'],result
        return result
    return get


@pytest.mark.parametrize('name',NAMES)
def test_dol_then_dip_restart_matches_independent_winding_physics(name,primary):
    from benchmarks.multibus_motor_validation import compare_si
    spec=example(name);p=primary(name);i=independent_circuit(spec).run(p)
    assert compare_si(spec,p,i)['passed']
    assert contract(name,spec,p)['passed']
    assert contract(name,spec,i)['passed']


@pytest.mark.parametrize('name',['manual_inhibit','restart_limit','thermal_inhibit'])
def test_protection_is_successful_calculation_with_expected_blocked_recovery(name,primary):
    r=primary(name);result=contract(name,example(name),r)
    assert result['passed']
    if name=='restart_limit':
        assert all(r['channels'][m.id+'.connected'][-1]==0 for m in example(name).coupled.motors)
        assert all(v['automatic_restarts']==1 for v in result['counts'].values())
    else:
        assert r['channels']['m.connected'][-1]==0
        assert result['counts']['m']['automatic_restarts']==0
        assert r['channels']['m2.connected'][-1]==1


@pytest.mark.parametrize('corruption',['nonfinite','leakage','state_jump','missing_side','deadline','counter','pending','coast','lost_sample'])
def test_audit_rejects_corrupted_physics_and_protection_trace(corruption,primary):
    r=copy.deepcopy(primary('gen_restart'));spec=example('gen_restart')
    if corruption=='nonfinite':r['channels']['m.speed_pu'][3]=float('nan')
    elif corruption=='leakage':r['channels']['m.current_pu'][0]=.01
    elif corruption=='counter':r['channels']['m.contactor_restart_count'][-1]=2
    elif corruption=='pending':r['channels']['m.contactor_pending_restart'][-1]=1
    elif corruption=='lost_sample':r['channels']['m.current_pu'].pop()
    elif corruption=='deadline':
        e=next(e for e in r['event_log'] if e.get('cause')=='automatic_restart');e['time_s']+=.001
    else:
        i=next(i for i,(t,s) in enumerate(zip(r['time_s'],r['sample_side'])) if t>1.8 and s=='post_event')
        if corruption=='missing_side':r['sample_side'][i]='regular'
        elif corruption=='state_jump':r['channels']['g.omega'][i]+=.01
        else:
            j=next(i for i,(t,s) in enumerate(zip(r['time_s'],r['sample_side'])) if abs(t-2.13)<1e-8 and s=='pre_event')
            for k in (j,j+1):r['channels']['m.speed_pu'][k]+=.01
    try:result=contract('gen_restart',spec,r)
    except ValueError:return
    assert not result['passed']


def test_open_stator_analytic_coast_checks_speed_and_rotor_flux_magnitude():
    motor=example('grid_restart').induction.motors[1];duration=.4;w0=.97
    expected=w0/(1+motor.fan_torque_pu/(2*motor.inertia_s)*w0*duration)
    tau=(motor.rotor_x_pu+motor.magnetizing_x_pu)/(2*math.pi*50*motor.rotor_r_pu)
    channels={motor.id+'.speed_pu':[w0,expected],motor.id+'.e_real_pu':[.9,.9*math.exp(-duration/tau)],motor.id+'.e_imag_pu':[0.,0.]}
    result=coast_check(motor,channels,np.array([0.,duration]),0,1,50.)
    assert result['speed_error_pu']==0 and result['emf_error_pu']==0
    channels[motor.id+'.speed_pu'][1]+=1e-3
    assert coast_check(motor,channels,np.array([0.,duration]),0,1,50.)['speed_error_pu']>9e-4


def test_restart_protocol_written_before_external_solver(tmp_path,monkeypatch):
    import json
    from benchmarks import multibus_restart_validation as v
    def stop(*args,**kwargs):
        p=json.loads((tmp_path/'protocol.json').read_text())
        assert p['native_steps_s']==[.00025,.000125,.0000625]
        assert p['physical_and_protection_limits']['coast_speed_pu']==1e-6
        assert p['event_time_limit_s']==1e-6
        raise RuntimeError('preflight')
    monkeypatch.setattr(v,'native_reference',stop)
    with pytest.raises(RuntimeError,match='preflight'):v.run(tmp_path,['gen_restart'])
    r=json.loads((tmp_path/'summary.json').read_text());assert not r['passed'] and r['status']=='failed'


def test_partial_or_failed_corpus_cannot_pass_offline_audit(tmp_path):
    from power_grid_env.report import write_json
    from benchmarks.multibus_restart_audit import audit
    write_json(tmp_path/'summary.json',dict(passed=True,full_registered_corpus=False))
    write_json(tmp_path/'protocol.json',dict(cases=['gen_restart']))
    with pytest.raises(ValueError,match='full passing'):audit(tmp_path)
