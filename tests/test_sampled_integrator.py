import math
from pathlib import Path

import numpy as np
import pytest

from power_grid_env.sampled_integrator import integrate_sampled
from power_grid_env.dynamic_motor_identification import DynamicMotorIdentification, WorkBudget, replay
from power_grid_env.study_artifacts import read_json, encoded, seal


def test_independent_exponential_solution_and_refinement_order():
    errors = []
    for n in (2, 4, 8):
        values, stats = integrate_sampled(np.linspace(0, 1, n + 1), [1.], lambda _: lambda t, x: x, rtol=.1, atol=.1)
        errors.append(abs(values[0, -1] - math.e))
        assert stats['knot_crossings'] == 0
    assert errors[1] < errors[0] / 16 and errors[2] < errors[1] / 16


def test_piecewise_affine_input_is_integrated_without_crossing_any_knot():
    times = np.array([0., .1, .101, .3]); voltages = np.array([0., 2., -3., 5.])
    def interval(i):
        def rhs(t, x):
            assert times[i] - 1e-15 <= t <= times[i + 1] + 1e-15
            return [voltages[i] + (voltages[i + 1] - voltages[i]) * (t - times[i]) / (times[i + 1] - times[i])]
        return rhs
    values, stats = integrate_sampled(times, [0.], interval)
    exact = np.r_[0., np.cumsum(np.diff(times) * (voltages[1:] + voltages[:-1]) / 2)]
    assert values[0] == pytest.approx(exact, abs=1e-14)
    assert stats['input_intervals'] == 3


def test_local_error_refinement_resolves_stiff_decay():
    values, stats = integrate_sampled([0., .001], [1.], lambda _: lambda t, x: -10000 * x)
    assert values[0, -1] == pytest.approx(math.exp(-10), abs=1e-10)
    assert stats['rejected_steps'] > 0 and stats['maximum_accepted_scaled_local_error'] <= 1


@pytest.mark.parametrize('times', [[0., 0.], [1., 0.], [0., float('nan')], [0.]])
def test_invalid_knots_rejected(times):
    with pytest.raises(ValueError): integrate_sampled(times, [0.], lambda _: lambda t, x: x)


@pytest.mark.parametrize('subdivisions', [0, 65, 1.5, True])
def test_invalid_subdivision_budget_rejected(subdivisions):
    with pytest.raises(ValueError): integrate_sampled([0., 1.], [0.], lambda _: lambda t, x: x, subdivisions=subdivisions)


def test_nonfinite_rhs_rejected():
    with pytest.raises(ValueError): integrate_sampled([0., 1.], [0.], lambda _: lambda t, x: [float('nan')])


@pytest.mark.parametrize('profile', ['linear', 'saturated', 'viscous', 'locked'])
@pytest.mark.parametrize('conditional', [True, False])
def test_physical_profiles_match_tight_independent_dop853(profile, conditional):
    path = Path(__file__).resolve().parents[1] / 'cases/dynamic-identification' / (profile + '.json')
    spec = DynamicMotorIdentification.model_validate(read_json(path)); trace = spec.fitting[0]
    actual = replay(spec.initial, trace, WorkBudget(spec), conditional=conditional, method='sample_rk4')
    reference = replay(spec.initial, trace, WorkBudget(spec), conditional=conditional, method='dop853', accuracy_factor=.001)
    for key, tolerance in [('current', 5e-7), ('speed', 5e-7), ('stator_flux', 1e-8), ('rotor_flux', 1e-8), ('torque', 1e-6)]:
        assert actual[key] == pytest.approx(reference[key], abs=tolerance), (profile, conditional, key)


def test_sampled_method_cannot_bypass_rhs_limit():
    from power_grid_env.dynamic_motor_identification import identify_dynamic
    spec = read_json(Path(__file__).resolve().parents[1] / 'cases/dynamic-identification/linear.json')
    spec.update(replay_method='sample_rk4', maximum_rhs_evaluations=1)
    result = identify_dynamic(spec)
    assert not result['completed'] and not result['passed'] and result['rhs_evaluations'] == 1


def test_old_recording_artifact_without_new_default_still_verifies(tmp_path):
    from tests.test_motor_recordings import write_protocol
    from power_grid_env.motor_recordings import import_recordings, verify_recordings
    path, _ = write_protocol(tmp_path)
    out = tmp_path / 'run'; import_recordings(path, out)
    spec = read_json(out / 'study.json'); spec.pop('replay_method')
    (out / 'study.json').write_bytes(encoded(spec)); (out / 'manifest.json').unlink(); seal(out)
    assert verify_recordings(out)['verified']


@pytest.fixture(scope='module')
def frozen_identification(tmp_path_factory):
    from power_grid_env.dynamic_identification_audit import run_audit_artifact
    root=Path(__file__).resolve().parents[1]
    spec=read_json(root/'cases/sampled-identification/frozen-b18/original-input.json')
    spec.update(replay_method='sample_rk4',maximum_rhs_evaluations=4000000,maximum_wall_s=120.)
    out=tmp_path_factory.mktemp('sampled-fit')/'run'
    return run_audit_artifact(dict(identification=spec),out),out


def test_frozen_noisy_voltage_failure_is_accepted_with_original_limits(frozen_identification):
    result,out=frozen_identification
    assert result['completed'] and result['passed'] and result['raw_residuals_passed'],result
    assert result['rhs_evaluations']<4000000
    assert result['replay_method']=='sample_rk4' and (out/'passport.json').exists()


def test_optimizer_exposes_cost_and_numeric_derivative_evaluations(frozen_identification):
    result,_=frozen_identification
    optimizer=result['optimizer']
    assert optimizer['residual_evaluations']==result['evaluations']
    assert optimizer['numeric_jacobian_residual_evaluations']==result['evaluations']-optimizer['nfev']
    assert optimizer['numeric_jacobian_residual_evaluations']==2*len(result['parameters'])*optimizer['njev']
