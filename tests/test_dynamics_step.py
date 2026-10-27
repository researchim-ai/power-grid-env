"""PM-00: якобиан неявной трапеции и отказ несходящегося шага."""

import math

import numpy as np
import pytest

from power_grid_env.dynamics import _trapezoid_jacobian, derivative, equilibrium, simulate_smib


def test_trapezoid_jacobian_matches_the_residual_derivative():
    pm, e_pu, v_pu, x_pu, inertia = 0.5, 1.1, 1.0, 0.3, 4.0
    omega_s = 2.0 * math.pi * 50.0
    delta0, omega0 = equilibrium(pm, e_pu, v_pu, x_pu, omega_s)
    state = np.array([delta0 + 0.2, omega0 + 0.5])
    step_s = 0.01

    def deriv(value):
        return derivative(value, pm, e_pu, v_pu, x_pu, inertia, 0.0, omega_s)

    previous = deriv(state)
    guess = state + step_s * previous
    jacobian = _trapezoid_jacobian(guess, deriv, step_s)
    # δ' = ω - ωs, поэтому ∂R_δ/∂ω = -h/2.
    assert jacobian[0, 1] == pytest.approx(-0.5 * step_s, abs=1e-8)
    residual = guess - state - 0.5 * step_s * (previous + deriv(guess))
    finite = np.zeros_like(jacobian)
    for column in range(2):
        bumped = guess.copy()
        bumped[column] += 1e-6
        changed = bumped - state - 0.5 * step_s * (previous + deriv(bumped))
        finite[:, column] = (changed - residual) / 1e-6
    assert np.max(np.abs(jacobian - finite)) < 1e-6


def test_unconverged_trapezoid_step_is_rejected():
    pm, e_pu, v_pu, x_pu, inertia = 0.5, 1.1, 1.0, 0.3, 4.0
    with pytest.raises(ValueError, match="не сошёлся"):
        simulate_smib(
            pm=pm,
            e_pu=e_pu,
            v_pu=v_pu,
            x_pu=x_pu,
            inertia_s=inertia,
            delta0=1.2,
            omega0=2.0 * math.pi * 50.0,
            dt_s=0.2,
            t_end_s=0.2,
            max_newton=1,
            tolerance=1e-14,
        )


def test_trapezoid_error_drops_by_about_four_when_the_step_is_halved():
    pm, e_pu, v_pu, x_pu, inertia = 0.5, 1.1, 1.0, 0.3, 4.0
    omega_s = 2.0 * math.pi * 50.0
    delta_eq, omega_eq = equilibrium(pm, e_pu, v_pu, x_pu, omega_s)
    reference = simulate_smib(
        pm=pm,
        e_pu=e_pu,
        v_pu=v_pu,
        x_pu=x_pu,
        inertia_s=inertia,
        delta0=delta_eq + 0.15,
        omega0=omega_eq,
        dt_s=5e-4,
        t_end_s=0.2,
    )
    errors = []
    for step_s in (0.02, 0.01, 0.005):
        coarse = simulate_smib(
            pm=pm,
            e_pu=e_pu,
            v_pu=v_pu,
            x_pu=x_pu,
            inertia_s=inertia,
            delta0=delta_eq + 0.15,
            omega0=omega_eq,
            dt_s=step_s,
            t_end_s=0.2,
        )
        errors.append(abs(float(coarse["delta"][-1]) - float(reference["delta"][-1])))
    assert errors[0] > errors[1] > errors[2]
    assert errors[0] / errors[1] == pytest.approx(4.0, rel=0.25)
    assert errors[1] / errors[2] == pytest.approx(4.0, rel=0.25)


def test_voltage_step_is_a_grid_point():
    pm, e_pu, v_pu, x_pu, inertia = 0.4, 1.1, 1.0, 0.35, 5.0

    def voltage_at(time_s: float) -> float:
        return 0.25 if 0.2 <= time_s < 0.45 else 1.0

    run = simulate_smib(
        pm=pm,
        e_pu=e_pu,
        v_pu=v_pu,
        x_pu=x_pu,
        inertia_s=inertia,
        damping=3.0,
        dt_s=0.03,
        t_end_s=0.6,
        v_profile=voltage_at,
        event_times=[0.2, 0.45],
    )
    assert any(abs(float(item) - 0.2) < 1e-12 for item in run["t"])
    assert any(abs(float(item) - 0.45) < 1e-12 for item in run["t"])
    assert run["converged"] is True
