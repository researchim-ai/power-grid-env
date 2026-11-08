"""RK4 step doubling on known input knots, with local error control.

The caller supplies one smooth RHS per input interval. No step crosses a
piecewise-linear input knot. Richardson correction uses the same five-state
physical equations, without filtering or changing the observed input.
"""
import numpy as np


def integrate_sampled(times, initial, interval_rhs, *, rtol=2e-9, atol=2e-11, subdivisions=1):
    times = np.asarray(times, dtype=float)
    state = np.asarray(initial, dtype=float).copy()
    if times.ndim != 1 or len(times) < 2 or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
        raise ValueError('finite strictly increasing integration knots required')
    if state.ndim != 1 or not np.isfinite(state).all() or not 0 < rtol < 1 or not 0 < atol < 1:
        raise ValueError('finite state and positive integration tolerances required')
    if type(subdivisions) is not int or not 1 <= subdivisions <= 64:
        raise ValueError('one to 64 initial subdivisions required')
    output = np.empty((len(state), len(times))); output[:, 0] = state
    accepted = rejected = 0
    maximum_depth = 0
    maximum_error = 0.

    def rk4(rhs, t, x, h):
        k1 = np.asarray(rhs(t, x))
        k2 = np.asarray(rhs(t + h / 2, x + h * k1 / 2))
        k3 = np.asarray(rhs(t + h / 2, x + h * k2 / 2))
        k4 = np.asarray(rhs(t + h, x + h * k3))
        return x + h * (k1 + 2 * k2 + 2 * k3 + k4) / 6

    def advance(rhs, t, x, h, depth):
        nonlocal accepted, rejected, maximum_depth, maximum_error
        coarse = rk4(rhs, t, x, h)
        half = rk4(rhs, t, x, h / 2)
        fine = rk4(rhs, t + h / 2, half, h / 2)
        correction = (fine - coarse) / 15
        scale = atol + rtol * np.maximum(np.abs(x), np.abs(fine))
        error = float(np.max(np.abs(correction) / scale))
        if not np.isfinite(fine).all() or not np.isfinite(error):
            raise ValueError('nonfinite sampled integration step')
        maximum_depth = max(maximum_depth, depth)
        if error <= 1:
            accepted += 1; maximum_error = max(maximum_error, error)
            return fine + correction
        rejected += 1
        if depth >= 14 or t + h / 2 in (t, t + h):
            raise ValueError('sampled integration refinement limit exceeded')
        middle = advance(rhs, t, x, h / 2, depth + 1)
        return advance(rhs, t + h / 2, middle, h / 2, depth + 1)

    for index, (left, right) in enumerate(zip(times[:-1], times[1:])):
        rhs = interval_rhs(index)
        boundaries = np.linspace(left, right, subdivisions + 1)
        for a, b in zip(boundaries[:-1], boundaries[1:]):
            state = advance(rhs, float(a), state, float(b - a), 0)
        output[:, index + 1] = state
    return output, dict(accepted_steps=accepted, rejected_steps=rejected,
                        maximum_refinement_depth=maximum_depth, maximum_accepted_scaled_local_error=maximum_error,
                        input_intervals=len(times) - 1, initial_subdivisions=subdivisions,
                        rtol=rtol, atol=atol, knot_crossings=0)
