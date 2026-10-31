"""Bounded high-speed equilibrium search for coupled induction machines.

A failed search does not prove absence of any physical equilibrium. The selected
branch must satisfy its speed bounds, ODE residual and local linear stability.
"""
from __future__ import annotations
import numpy as np
from scipy.optimize import least_squares

MAX_EVALUATIONS = 2000


def initialize_motor_state(model):
    settings = model.settings
    state = np.r_[np.zeros(model.electrical_size), [m.thermal.initial_level for m in settings.motors if m.thermal],np.zeros(model.motor_state_end-model.electrical_size-len(model.thermal_indices))]
    requested = settings.operating_point
    if requested is None:
        return state, {'mode': 'cold_start', 'thermal_state': 'passport'}
    active = np.flatnonzero(model.connected)
    if not len(active):
        return None, {'reason': 'no_connected_motor_for_operating_point'}
    speed = np.zeros(len(settings.motors))
    evaluations = 0
    best_residual = float('inf')
    rejected = []
    def residual(values):
        nonlocal evaluations, best_residual
        evaluations += 1
        if evaluations > MAX_EVALUATIONS:
            raise RuntimeError('operating point evaluation budget exceeded')
        speed[active] = values
        _, _, _, torque = model.steady_electrical(speed)
        error = np.array([(torque[i]-settings.motors[i].load_torque(speed[i]))/(2*settings.motors[i].inertia_s) for i in active])
        best_residual = min(best_residual, float(max(abs(error))))
        return error
    selected = None
    try:
        for guess in (.99, .97, .9, (requested.minimum_speed_pu+1)/2):
            initial = np.full(len(active), np.clip(guess, requested.minimum_speed_pu+1e-10*(1-requested.minimum_speed_pu), 1-1e-10*(1-requested.minimum_speed_pu)))
            candidate = least_squares(residual, initial, bounds=(requested.minimum_speed_pu, 1.),
                                      xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=200)
            values = candidate.x
            if max(abs(residual(values))) > requested.tolerance:
                # A constrained solver approaches boundary roots only
                # asymptotically (e.g. an unloaded motor at synchronous speed).
                # Evaluate the actual boundary, never accept by distance alone.
                boundary = values.copy()
                distance = 1e-5*(1-requested.minimum_speed_pu)
                boundary[1-boundary < distance] = 1.
                boundary[boundary-requested.minimum_speed_pu < distance] = requested.minimum_speed_pu
                if np.array_equal(boundary, values) or max(abs(residual(boundary))) > requested.tolerance:
                    continue
                values = boundary
            speed[active] = values
            voltage, current, internal, _ = model.steady_electrical(speed)
            state[:model.electrical_size] = np.column_stack([internal.real, internal.imag, speed]).ravel()
            model.cages.initialize(state,current,speed)
            if requested.thermal_state == 'equilibrium':
                for i, address in model.thermal_indices.items():
                    state[address] = abs(current[i])**2
            # Algebraic stator elimination must agree with the ordinary RHS.
            derivative = model.rhs(0., state)
            dynamic_indices=[*range(model.electrical_size),*[k for address in model.cages.addresses.values() for k in range(address.start,address.stop)]]
            dynamic_residual = float(max(abs(derivative[dynamic_indices])))
            if not np.isfinite(derivative).all() or dynamic_residual > requested.tolerance:
                rejected.append('dynamic_residual')
                continue
            indices = np.array([*[3*i+j for i in active for j in range(3)],*[k for i,address in model.cages.addresses.items() if i in active for k in range(address.start,address.stop)]])
            jacobian = model.jacobian(0.,state)[np.ix_(indices,indices)]
            modes = np.linalg.eigvals(jacobian)
            abscissa = float(max(modes.real))
            if not np.isfinite(modes).all() or abscissa >= -1e-7:
                rejected.append('unstable_or_near_axis_operating_point')
                continue
            thermal_limits = [settings.motors[i].id for i, address in model.thermal_indices.items()
                              if state[address] >= settings.motors[i].thermal.trip_level]
            if thermal_limits:
                return None, {'reason': 'initial_thermal_limit', 'motors': thermal_limits}
            selected = {'mode': requested.method, 'thermal_state': requested.thermal_state,
                        'evaluations': evaluations, 'dynamic_residual_per_s': dynamic_residual,
                        'thermal_residual_per_s': {settings.motors[i].id: float(derivative[address]) for i, address in model.thermal_indices.items()},
                        'speed_pu': {m.id: float(speed[i]) for i, m in enumerate(settings.motors)},
                        'bus_voltage_pu': {bus: float(abs(voltage[i])) for i, bus in enumerate(model.bus_names)},
                        'connected_machine_spectral_abscissa_per_s': abscissa,
                        'local_stability': 'asymptotically_stable_linearized',
                        'branch_note': 'bounded local high-speed search; uniqueness and global stability not proven'}
            break
    except (ValueError, RuntimeError, ArithmeticError, np.linalg.LinAlgError) as exc:
        return None, {'reason': 'operating_point_search_failed', 'detail': str(exc), 'evaluations': evaluations}
    if selected is None:
        return None, {'reason': 'no_qualified_operating_point_found', 'evaluations': evaluations,
                      'best_speed_derivative_residual_per_s': best_residual if np.isfinite(best_residual) else None,
                      'rejected_candidates': rejected, 'minimum_speed_pu': requested.minimum_speed_pu}
    return state.copy(), selected
