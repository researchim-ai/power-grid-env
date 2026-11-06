import os
from pathlib import Path
import numpy as np
import pytest
from pydantic import ValidationError
from benchmarks.extended_equipment_validation import verify_native
from benchmarks.motor_identification_reference import example_identification
from power_grid_env.motor_identification import identify,MotorIdentification

@pytest.fixture(scope='module')
def observations():
    wheel=os.environ.get('PGE_MOTULATOR_WHEEL')
    if not wheel:pytest.skip('explicit native wheel required for independent generated observations')
    verify_native(Path(wheel));return example_identification()


def test_recovers_native_electrical_saturation_and_core_parameters_on_held_out_rows(observations):
    expected,spec=observations;result=identify(spec);assert result['completed'] and result['passed'],result
    p=result['passport']
    for key in ['stator_r_ohm','unsaturated_h','saturation_beta_per_vs','core_loss_s']:assert p[key]==pytest.approx(getattr(expected,key),rel=1e-7)
    assert p['cages'][0]['resistance_ohm']==pytest.approx(expected.cages[0].resistance_ohm,rel=1e-7)
    assert not result['field_validated'] and result['held_out_rows']==18


def test_degenerate_excitation_is_not_promoted_to_identification(observations):
    _,spec=observations;s=spec.model_copy(deep=True);s.fitting=[s.fitting[0]]*6;s.validation=[s.validation[0]]*4
    r=identify(s);assert not r['passed'] and not r.get('identifiable',False),r


def test_bad_held_out_data_is_rejected_even_when_training_fits(observations):
    _,spec=observations;s=spec.model_copy(deep=True);s.validation[0].current_alpha_a+=1.
    r=identify(s);assert not r['passed'] and r['maximum_validation_error_sigma']>s.validation_tolerance_sigma

@pytest.mark.parametrize('defect',['units','leakage','thermal'])
def test_invalid_identification_protocol_fails_closed(observations,defect):
    _,spec=observations;payload=spec.model_dump(mode='json')
    if defect=='units':payload['coordinates']='RMS_unspecified'
    elif defect=='leakage':payload['validation'][0]=payload['fitting'][0]
    else:payload['initial']['thermal']=dict(stator_capacity_j_per_k=100,rotor_capacity_j_per_k=100,stator_ambient_w_per_k=1,rotor_ambient_w_per_k=1)
    with pytest.raises(ValidationError):MotorIdentification.model_validate(payload)


def test_all_numerical_Jacobian_evaluations_are_bounded(observations):
    _,s=observations;s=s.model_copy(deep=True);s.maximum_evaluations=1
    result=identify(s);assert not result['passed'] and result['status']=='runtime_limit_exceeded'
    assert result['evaluations']==1


def test_changing_only_weights_does_not_hide_holdout_leakage(observations):
    _,s=observations;payload=s.model_dump(mode='json');payload['validation'][0]=payload['fitting'][0].copy();payload['validation'][0]['sigma_a']*=2
    with pytest.raises(ValidationError,match='distinct'):MotorIdentification.model_validate(payload)
