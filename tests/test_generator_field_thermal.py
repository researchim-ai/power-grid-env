"""Field rotor memory, independent winding reference and exact lifted ODE."""
from functools import lru_cache
import math
import numpy as np
import pytest
from pydantic import ValidationError
from benchmarks.generator_field_cases import example,NAMES
from benchmarks.generator_field_validation import compare,field_trips
from benchmarks.multibus_operating_point import independent_circuit
from power_grid_env.study_spec import run_study
from power_grid_env.generator_field_thermal import GeneratorFieldThermalProtection


@pytest.fixture(scope='module')
def traces():
    @lru_cache(None)
    def get(name):
        s=example(name);r=run_study(s,isolate=True,wall_timeout_s=90.);assert r['completed'],r
        return s,r,independent_circuit(s).run(r)
    return get


@pytest.mark.parametrize('name',NAMES)
def test_full_field_thermal_trajectory_matches_SI_and_postquench_exponential(name,traces):
    s,r,i=traces(name);assert compare(s,r,i)['passed']
    assert len(field_trips(r))==(0 if name=='equilibrium_hold' else 2 if name=='two_relays' else 1)


@pytest.mark.parametrize('name',['cold','warm'])
def test_equilibrium_field_constant_heating_trip_time_has_closed_form(name,traces):
    s,r,i=traces(name);p=s.coupled.generator_field_thermal[0]
    heat=r['channels']['g2.field_current_pu'][0]**2
    expected=p.time_constant_s*math.log((heat-p.initial_level)/(heat-p.trip_level))
    assert field_trips(r)[0]['time_s']==pytest.approx(expected,abs=1e-8)


def test_zero_impressed_voltage_preserves_nonzero_current_and_thermal_memory(traces):
    s,r,i=traces('cold');trip=field_trips(r)[0]['time_s']
    at=next(j for j,(t,side) in enumerate(zip(r['time_s'],r['sample_side'])) if t==trip and side=='post_event')
    ch=r['channels'];assert ch['g2.field_pu'][at]==0 and ch['g2.SEXS.field_pu'][at]>0
    assert ch['g2.field_current_pu'][at]>.1 and ch['g2.field_thermal_level'][at]==pytest.approx(.8)
    dt=r['time_s'][-1]-trip
    assert ch['g2.field_thermal_level'][-1]>.8*math.exp(-dt/.1)+.01


def test_field_trip_remains_enabled_after_manual_stator_open(traces):
    s,r,i=traces('manual_stator_open')
    assert field_trips(r)[0]['stator_was_closed'] is False
    assert field_trips(r)[0]['time_s']>.005


@pytest.mark.parametrize('defect',['unknown','duplicate','hot','field_base'])
def test_invalid_field_configuration_is_rejected(defect):
    s=example('OEL_coordination');p=s.coupled.generator_field_thermal[0]
    if defect=='unknown':p.generator_id='absent'
    elif defect=='duplicate':s.coupled.generator_field_thermal*=2
    elif defect=='hot':p.initialization='equilibrium';p.trip_level=.1
    else:p.rated_field_reaction_pu=2.
    r=run_study(s);assert not r['completed']


def test_field_thermal_modal_inference_and_native_worker_are_explicitly_rejected(monkeypatch):
    import benchmarks.andes_multibus_motor as native
    monkeypatch.setattr(native.subprocess,'Popen',lambda *a,**kw:pytest.fail('unsupported native worker'))
    with pytest.raises(ValueError,match='field thermal'):native.native_reference(example('cold'),.00025)
    s=example('cold');s.study='coupled_small_signal'
    assert run_study(s)['reason']=='generator_field_thermal_modal_analysis_unsupported'


@pytest.mark.parametrize('field,value',[('time_constant_s',0),('rated_field_reaction_pu',0),('trip_level',0),('initial_level',-1),('action','instantaneous_current_zero')])
def test_invalid_physical_field_passport_rejected(field,value):
    data=example('cold').coupled.generator_field_thermal[0].model_dump();data[field]=value
    with pytest.raises(ValidationError):GeneratorFieldThermalProtection.model_validate(data)
