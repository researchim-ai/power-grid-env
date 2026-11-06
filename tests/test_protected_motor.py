import pytest
import numpy as np
from benchmarks.protected_motor_cases import case
from power_grid_env.protected_motor import ProtectedMotorStudy,ProtectedCircuit,run_protected_motor


def short():
    s=case('aalto-saturated-fan__restart');s.duration_s=.02;s.maximum_runup_s=.015
    for source in s.case.sources:source.envelope=[(0.,1.)]
    return s


def test_contact_opening_preserves_all_states_and_has_passive_clamp():
    s=short();m=ProtectedCircuit(s);x=m.initial();x[:m.nd]=[.01]*m.nd
    before=x.copy();u,ic=m.clamp(.01,x)
    assert np.all(u*ic>=0)
    m.contacts_open();np.testing.assert_array_equal(x,before)
    assert all(r.resistance_ohm==s.off_resistance_ohm for r in m.contacts)


def test_full_winding_relay_has_conservative_energy_and_explicit_limits():
    r=run_protected_motor(short());assert r['completed'],r
    assert r['maximum_kcl_a']<1e-6 and r['maximum_energy_relative_residual']<2e-5
    assert not r['scope']['flux_or_current_discarded'] and not r['scope']['ideal_galvanic_isolation']
    assert r['engineering']['classification']=='not_accelerated'


def test_three_pole_commands_and_actual_capacitance_are_required():
    s=case('aalto-saturated-fan__locked');data=s.model_dump(mode='json');data['case']['events']=data['case']['events'][:1]
    with pytest.raises(ValueError,match='three-pole'):ProtectedMotorStudy.model_validate(data)
    s=short();s.case.capacitors=[]
    with pytest.raises(ValueError):ProtectedCircuit(s)


def test_thermal_guard_must_be_within_passport_validity():
    s=case('aalto-saturated-viscous-thermal__temperature');data=s.model_dump(mode='json');data['relay']['stator_trip_c']=300.
    with pytest.raises(ValueError,match='valid range'):ProtectedMotorStudy.model_validate(data)


def test_budget_failure_cannot_return_engineering_success():
    s=short();s.runtime_limits.maximum_rhs_evaluations=1;r=run_protected_motor(s)
    assert not r['completed'] and 'engineering' not in r
