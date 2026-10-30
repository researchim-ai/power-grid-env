"""Frozen parameter points, real workers and conservative engineering outcomes."""
from pathlib import Path
import copy
import hashlib
import json
import numpy as np
import pytest
from pydantic import ValidationError
from power_grid_env.model import canonical_hash
from power_grid_env.induction import InductionEvent
from power_grid_env.motor_sensitivity import (MotorSensitivityProtocol, ParameterAxis, MotorAssessment,
    RecoveryWindow, build_parameter_points, run_motor_sensitivity, _assess)
from power_grid_env.study_spec import StudySpec, run_study


def example():
    s=StudySpec.model_validate_json(Path('cases/studies/operating_fan_hold.json').read_text())
    s.induction.duration_s=.05
    return s


def protocol(**updates):
    data=dict(id='test-screen',axes=[ParameterAxis(id='H',parameter='inertia_s',equipment_id='m',factors=[1.,2.])],
              observations=[MotorAssessment(motor_id='m',minimum_final_speed_pu=.95,minimum_bus_voltage_pu=.9,maximum_current_pu=2.)],
              total_timeout_s=60.)
    data.update(updates)
    return MotorSensitivityProtocol(**data)


def test_design_builds_independent_variants_and_does_not_mutate_inputs():
    spec=example(); before=canonical_hash(spec)
    cfg=protocol(axes=[ParameterAxis(id='H',parameter='inertia_s',equipment_id='m',factors=[.8,1.,1.2]),
                       ParameterAxis(id='grid',parameter='source_series_impedance',equipment_id='source',factors=[1.,2.])])
    points=build_parameter_points(spec,cfg)
    assert len(points)==4 and points[0]['nominal']
    assert canonical_hash(spec)==before
    assert points[1]['study'].induction.motors[0].inertia_s==pytest.approx(.8*spec.induction.motors[0].inertia_s)
    assert points[-1]['study'].network.sources[0].x_ohm==2*spec.network.sources[0].x_ohm
    assert points[-1]['study'].induction.motors[0].inertia_s==spec.induction.motors[0].inertia_s
    cfg.design='cartesian'
    product=build_parameter_points(spec,cfg)
    assert len(product)==6 and sum(p['nominal'] for p in product)==1
    assert {tuple(p['factors'].values()) for p in product}=={(h,z) for h in [.8,1.,1.2] for z in [1.,2.]}


def test_target_types_scaling_and_schema_are_explicit():
    s=example()
    cfg=protocol(axes=[ParameterAxis(id='line',parameter='line_series_impedance',equipment_id='line',factors=[1.,2.]),
                       ParameterAxis(id='load',parameter='mechanical_torque',equipment_id='m',factors=[1.,1.5])])
    points=build_parameter_points(s,cfg)
    assert points[1]['study'].network.lines[0].x_ohm== (np.array(s.network.lines[0].x_ohm)*2).tolist()
    assert points[1]['study'].network.lines[0].b_siemens==s.network.lines[0].b_siemens
    assert points[2]['study'].induction.motors[0].fan_torque_pu==pytest.approx(1.5*s.induction.motors[0].fan_torque_pu)
    with pytest.raises(ValidationError):
        ParameterAxis(id='bad',parameter='inertia_s',equipment_id='m',factors=[.8,1.2])
    with pytest.raises(ValidationError):
        ParameterAxis(id='bad',parameter='inertia_s',equipment_id='m',factors=[1.,1.])
    with pytest.raises(ValidationError):
        ParameterAxis(id='bad',parameter='inertia_s',equipment_id='m',factors=[0.,1.])
    with pytest.raises(ValidationError):
        protocol(axes=[ParameterAxis(id=str(i),parameter=key,equipment_id='m',factors=[1.,2.,3.]) for i,key in enumerate(
            ['inertia_s','rotor_r_pu','stator_r_pu','rotor_x_pu'])],design='cartesian')
    bad=protocol();bad.axes[0].equipment_id='absent'
    with pytest.raises(ValueError,match='target'):
        build_parameter_points(s,bad)
    thermal=protocol(axes=[ParameterAxis(id='thermal',parameter='thermal_trip_level',equipment_id='m',factors=[1.,2.])])
    with pytest.raises(ValueError,match='thermal'):
        build_parameter_points(s,thermal)


def test_real_workers_preserve_equilibrium_inertia_invariance_and_manifests(tmp_path):
    spec=example();r=run_motor_sensitivity(spec,protocol(),tmp_path/'experiment')
    assert r['completed'] and r['all_cases_within_limits'],r
    assert r['engineering_assessment']=='within_sampled_limits'
    assert len(r['cases'])==2
    first,second=[c['observations']['m']['metrics'] for c in r['cases']]
    assert first['final_speed_pu']==pytest.approx(second['final_speed_pu'],abs=1e-8)
    assert first['minimum_sampled_bus_voltage_pu']==pytest.approx(second['minimum_sampled_bus_voltage_pu'],abs=1e-8)
    assert abs(r['cases'][1]['delta_from_baseline']['m']['final_speed_pu'])<1e-8
    assert not r['field_validated'] and not r['industrial_profile_qualified']
    manifest=json.loads((tmp_path/'experiment/manifest.json').read_text())
    for path,digest in manifest.items():
        assert hashlib.sha256((tmp_path/'experiment'/path).read_bytes()).hexdigest()==digest
    saved=StudySpec.model_validate_json((tmp_path/'experiment/case-001/study.json').read_text())
    assert saved.induction.motors[0].inertia_s==2*spec.induction.motors[0].inertia_s
    assert r['cases'][1]['study_sha256']==canonical_hash(saved)
    assert not (tmp_path/'experiment/.sensitivity.lock').exists()
    with pytest.raises(ValueError,match='directory'):
        run_motor_sensitivity(spec,protocol(),tmp_path/'experiment')
    assert json.loads((tmp_path/'experiment/run.json').read_text())==r


def test_completed_experiment_can_be_outside_engineering_limits(tmp_path):
    cfg=protocol();cfg.observations[0].minimum_final_speed_pu=1.
    r=run_motor_sensitivity(example(),cfg,tmp_path/'outside')
    assert r['completed'] and not r['all_cases_within_limits']
    assert r['engineering_assessment']=='outside_sampled_limits'
    assert all(c['numerically_completed'] for c in r['cases'])
    assert all(not c['observations']['m']['checks']['final_speed'] for c in r['cases'])


def test_failed_operating_point_is_inconclusive_not_zero_or_success(tmp_path):
    cfg=protocol(axes=[ParameterAxis(id='load',parameter='mechanical_torque',equipment_id='m',factors=[1.,1000.])])
    r=run_motor_sensitivity(example(),cfg,tmp_path/'failure')
    assert not r['completed'] and not r['all_cases_within_limits']
    assert r['engineering_assessment']=='inconclusive'
    assert r['cases'][0]['numerically_completed']
    assert r['cases'][1]['status']=='initialization_failed'
    assert r['cases'][1]['observations'] is None and r['cases'][1]['delta_from_baseline'] is None
    assert r['sampled_envelope']['m']['final_speed_pu']['available_cases']==1


def test_schema_invalid_variant_is_recorded_without_hiding_remaining_cases(tmp_path):
    s=example();from power_grid_env.motor_thermal import MotorThermalProtection
    s.induction.motors[0].thermal=MotorThermalProtection(time_constant_s=1.,trip_level=2.,initial_level=1.)
    cfg=protocol(axes=[ParameterAxis(id='thermal',parameter='thermal_trip_level',equipment_id='m',factors=[1.,.4,2.])])
    r=run_motor_sensitivity(s,cfg,tmp_path/'invalid')
    assert r['attempted_cases']==3 and not r['completed']
    assert r['cases'][1]['status']=='invalid_variant' and r['cases'][1]['observations'] is None
    assert r['cases'][2]['numerically_completed']


def test_recovery_uses_post_event_state_and_ignores_a_pre_open_limit():
    s=example();s.induction.duration_s=.4;s.induction.events=[InductionEvent(time_s=.2,kind='motor_open',equipment_id='m')]
    report=run_study(s);assert report['completed']
    criterion=MotorAssessment(motor_id='m',minimum_final_speed_pu=0.,minimum_bus_voltage_pu=0.,maximum_current_pu=10.,require_connected_at_end=False,
        recovery=RecoveryWindow(from_s=0.,minimum_speed_pu=.95,dwell_s=.2,maximum_delay_s=.4))
    assessed=_assess(s,report,criterion)
    assert assessed['metrics']['recovery_confirmed_s'] is None
    assert not assessed['checks']['recovery']
    # A held steady machine confirms the dwell at a saved sample.
    s.induction.events=[];report=run_study(s)
    assessed=_assess(s,report,criterion)
    assert assessed['metrics']['recovery_confirmed_s']==pytest.approx(.2)
    assert assessed['checks']['recovery']


def test_cancel_timeout_and_output_budget_remain_inconclusive(tmp_path):
    spec=example()
    cancelled=run_motor_sensitivity(spec,protocol(),tmp_path/'cancel',cancelled=lambda:True)
    assert cancelled['status']=='cancelled' and cancelled['attempted_cases']==0
    assert cancelled['engineering_assessment']=='inconclusive'
    total=run_motor_sensitivity(spec,protocol(total_timeout_s=.0001),tmp_path/'total-timeout')
    assert total['status']=='total_timeout' and not total['completed']
    cfg=protocol(per_case_timeout_s=.0001)
    timed=run_motor_sensitivity(spec,cfg,tmp_path/'timeout')
    assert not timed['completed'] and all(c['status']=='timeout' for c in timed['cases'])
    assert timed['engineering_assessment']=='inconclusive'
    bulky=spec.model_copy(deep=True);bulky.network.name='N'*1_000_000
    budget=run_motor_sensitivity(bulky,protocol(maximum_output_bytes=1_000_000),tmp_path/'budget')
    assert budget['status']=='output_budget_exceeded' and budget['attempted_cases']==0
    assert sum(p.stat().st_size for p in (tmp_path/'budget').rglob('*') if p.is_file())<=1_000_000


def test_orchestration_error_never_leaves_a_successful_journal(tmp_path,monkeypatch):
    import power_grid_env.motor_sensitivity as module
    def failed(*args,**kwargs):
        raise RuntimeError('test runner failure')
    monkeypatch.setattr(module,'run_study',failed)
    r=run_motor_sensitivity(example(),protocol(),tmp_path/'error')
    assert r['status']=='orchestration_failed' and not r['completed']
    assert json.loads((tmp_path/'error/run.json').read_text())['engineering_assessment']=='inconclusive'
    assert not (tmp_path/'error/.sensitivity.lock').exists()


def test_cli_and_exported_protocol_schema(tmp_path):
    spec=example();cfg=protocol()
    study=tmp_path/'study.json';study.write_text(spec.model_dump_json())
    config=tmp_path/'protocol.json';config.write_text(cfg.model_dump_json())
    from power_grid_env.cli import main
    args=['motor-sensitivity','--study',str(study),'--protocol',str(config),'--out',str(tmp_path/'run')]
    assert main(args)==0
    assert main(args)==2  # existing result must survive unchanged
    from power_grid_env.schema_export import export_schemas
    export_schemas(tmp_path/'schemas')
    from jsonschema import validate
    validate(cfg.model_dump(mode='json'),json.loads((tmp_path/'schemas/motor-sensitivity.schema.json').read_text()))


def test_running_experiment_freezes_input_protocol_and_study(tmp_path):
    spec=example();cfg=protocol();original=canonical_hash(spec)
    def mutate_caller():
        cfg.observations[0].minimum_final_speed_pu=1.
        spec.induction.motors[0].inertia_s=100.
        return False
    result=run_motor_sensitivity(spec,cfg,tmp_path/'frozen',cancelled=mutate_caller)
    assert result['completed'] and result['all_cases_within_limits'],result
    assert result['protocol']['observations'][0]['minimum_final_speed_pu']==.95
    assert result['base_study_sha256']==original
    saved=StudySpec.model_validate_json((tmp_path/'frozen/base-study.json').read_text())
    assert saved.induction.motors[0].inertia_s!=spec.induction.motors[0].inertia_s
