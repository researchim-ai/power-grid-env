import numpy as np
import pytest
from benchmarks.electrothermal_fault_cases import jobs
from benchmarks.electrothermal_frozen_fault import freeze_initial
from power_grid_env.phase_network import MotorPort
from power_grid_env.phase_core_motor import PhaseCoreMotor


@pytest.mark.parametrize('cages', [1, 3])
def test_frozen_control_has_same_initial_rhs_then_diverges_only_as_temperature_changes(cages):
    doc = next(j['study'] for j in jobs() if j['cages'] == cages and j['start'] == 'hot_rotating' and j['fault'] == 'll' and j['feedback'])
    fixed = freeze_initial(doc)
    a, b = [PhaseCoreMotor(MotorPort.model_validate(d['case']['motors'][0])) for d in [doc, fixed]]
    x, y = a.initial(), b.initial()
    np.testing.assert_array_equal(x, y)
    np.testing.assert_allclose(a.rates(0., 100.+30j, x)[0], b.rates(0., 100.+30j, y)[0], rtol=0, atol=1e-11)
    for model, state in [(a, x), (b, y)]:
        state[-2:] += 10.
    qa, qb = a.rates(0., 100.+30j, x)[1], b.rates(0., 100.+30j, y)[1]
    assert qa['stator_temperature_c'] == qb['stator_temperature_c'] == 100.
    assert qa['stator_resistance_ohm'] > qb['stator_resistance_ohm']
    assert qa['rotor1_resistance_ohm'] > qb['rotor1_resistance_ohm']
