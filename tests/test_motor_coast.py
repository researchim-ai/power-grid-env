"""Machine-side terminal reporting and exact coast/reclose acceptance checks."""
from pathlib import Path
import numpy as np
import pytest
from benchmarks.motor_coast_reference import compare
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.induction import InductionEvent


def example(name='long'):
    return StudySpec.model_validate_json(Path('cases/studies/motor_coast_'+name+'.json').read_text())


@pytest.mark.parametrize('name',['short','long','viscous','no_load'])
def test_open_stator_exact_coast_and_independent_SI_reclose(name):
    result=compare(example(name));assert result['passed'],result
    diagnostic=result['reclose_diagnostics'][0]['reclose_diagnostic']
    assert not diagnostic['pre_batch_connected'] and diagnostic['post_batch_connected']
    assert diagnostic['pre_batch_voltage_difference_pu']>0
    assert diagnostic['post_batch_current_pu']>0
    assert not result['field_validated']


def test_machine_terminal_voltage_is_residual_EMF_while_bus_stays_energized():
    s=example();r=run_study(s);assert r['completed'];c=r['channels'];off=np.array(c['m.connected'])==0
    e=np.array(c['m.e_real_pu'])+1j*np.array(c['m.e_imag_pu'])
    terminal=np.array(c['m.terminal_e_real_pu'])+1j*np.array(c['m.terminal_e_imag_pu'])
    assert np.max(abs(terminal[off]-e[off]))<1e-14
    assert np.min(np.array(c['m.bus_voltage_pu'])[off])>.9
    assert np.min(np.array(c['m.terminal_voltage_pu'])[off])<.5
    assert np.max(abs(np.array(c['m.current_pu'])[off]))==0
    assert np.max(np.array(c['m.breaker_voltage_difference_pu'])[~off])==0
    opened=s.induction.events[0].time_s
    pre=next(i for i,(t,side) in enumerate(zip(r['time_s'],r['sample_side'])) if t==opened and side=='pre_event')
    for key in ['speed_pu','e_real_pu','e_imag_pu']:
        assert c['m.'+key][pre]==c['m.'+key][pre+1]


def test_reclose_diagnostic_is_pre_batch_and_zero_EMF_has_no_defined_phase():
    s=example();s.induction.operating_point=None;s.induction.motors[0].connected=False
    src=s.network.sources[0].id
    s.induction.events=[InductionEvent(time_s=.1,kind='source_voltage',equipment_id=src,value=.6),InductionEvent(time_s=.1,kind='motor_close',equipment_id='m')]
    r=run_study(s);assert r['completed'];e=next(e for e in r['event_log'] if e['kind']=='motor_close');d=e['reclose_diagnostic']
    assert d['pre_batch_terminal_voltage_pu']==0. and d['pre_batch_phase_difference_rad'] is None
    assert d['pre_batch_bus_voltage_pu']==pytest.approx(1.)
    assert d['post_batch_connected'] and d['post_batch_current_pu']>0


def test_thermal_blocked_reclose_is_reported_without_bypassing_latch():
    s=StudySpec.model_validate_json(Path('cases/studies/thermal_blocked_reclose.json').read_text());r=run_study(s);assert r['completed']
    e=next(e for e in r['event_log'] if e['kind']=='motor_close');assert e['outcome']=='blocked_thermal_latch'
    assert not e['reclose_diagnostic']['post_batch_connected'] and e['reclose_diagnostic']['post_batch_current_pu']==0


def test_measured_voltage_location_selects_machine_side_and_cannot_silently_fallback(tmp_path):
    import csv,hashlib
    from power_grid_env.motor_trace import MotorTraceProtocol,compare_motor_trace,validate_motor_result
    s=example();r=run_study(s);assert r['completed']
    t,values=validate_motor_result(s,r,'m','motor_side')
    # Exact event timestamps use post-event limits, matching the CSV contract.
    indices=[i for i in range(len(t)) if i==len(t)-1 or t[i+1]!=t[i]]
    path=tmp_path/'machine-side.csv'
    with path.open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['time_s','current_a','voltage_ll_v','speed_pu'])
        for i in indices:writer.writerow([t[i],*values[i]])
    cfg=MotorTraceProtocol(study_id=s.id,motor_id='m',dataset_id='synthetic-terminal-selection',source_uri='test-fixture',source_type='synthetic_reference',csv_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),used_for_parameter_fitting=False,voltage_measurement_location='motor_side',maximum_sample_gap_s=.01,tolerances=dict(current_a=1e-6,voltage_ll_v=1e-6,speed_pu=1e-8))
    assert compare_motor_trace(s,r,path,cfg)['passed']
    cfg.voltage_measurement_location='grid_side'
    mismatch=compare_motor_trace(s,r,path,cfg);assert not mismatch['passed'] and not mismatch['metrics']['voltage_ll_v']['passed']
    del r['channels']['m.terminal_voltage_pu'];cfg.voltage_measurement_location='motor_side'
    with pytest.raises(ValueError,match='terminal_voltage_pu'):compare_motor_trace(s,r,path,cfg)
