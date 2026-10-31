"""SI rotor-flux reference with a separate thermal state and first-crossing trip.

Declared one-motor, one-source, one-line fixtures; no automatic reclose/reset.
This does not call the production state derivative, event loop or network stamps.
"""
from pathlib import Path
import argparse
import numpy as np
from scipy.integrate import solve_ivp
from benchmarks.induction_reference import circuit_parameters, initial_flux_reference, mechanical_torque_reference
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.report import write_json, sha256_file


def reference(spec, report):
    m, wb, rs, rr, lm, ls, lr, feed, source_v = circuit_parameters(spec)
    thermal = m.thermal
    assert thermal is not None and len(spec.induction.motors) == 1 and m.contactor is None
    connected, scale, latched = m.connected, 1., False
    load_scale = 1.
    vb = m.rated_ll_v/np.sqrt(3)
    ib = m.rated_va/(3*vb)
    matrix = np.array([[rs+1j*wb*ls+feed, 1j*wb*lm], [lm, lr]])
    def current(x):
        flux = x[0]+1j*x[1]
        if connected:
            stator, rotor = np.linalg.solve(matrix, [source_v*scale, flux])
        else:
            stator, rotor = 0j, flux/lr
        return stator, rotor
    def rhs(time, x):
        stator, rotor = current(x)
        flux = x[0]+1j*x[1]
        dflux = -rr*rotor-1j*wb*(1-x[2])*flux
        torque = -3*wb*lm*(rotor*np.conj(stator)).imag/m.rated_va
        return [dflux.real, dflux.imag,
                (torque-load_scale*mechanical_torque_reference(m, x[2]))/(2*m.inertia_s),
                ((abs(stator)/ib)**2-x[3])/thermal.time_constant_s]
    def crossing(time, x):
        return x[3]-thermal.trip_level
    crossing.terminal = True
    crossing.direction = 1
    events = {}
    for event in spec.induction.events:
        events.setdefault(event.time_s, []).append(event)
    boundaries = [0., *sorted(events), spec.induction.duration_s]
    segments, changes = [], []
    state = np.r_[initial_flux_reference(spec), thermal.initial_level]
    if spec.induction.operating_point and spec.induction.operating_point.thermal_state == 'equilibrium':
        state[3] = (abs(current(state)[0])/ib)**2
    trip_time = None
    for left, right in zip(boundaries, boundaries[1:]):
        cursor = left
        while cursor < right:
            sol = solve_ivp(rhs, (cursor, right), state, method='DOP853', rtol=2e-11, atol=1e-12,
                            max_step=min(.0005, spec.induction.step_s/2), dense_output=True,
                            events=None if latched else crossing)
            assert sol.success, sol.message
            end = float(sol.t[-1])
            segments.append((cursor, end, sol.sol, connected, scale, latched))
            state = sol.y[:, -1]
            trip = sol.status == 1
            commands = events.get(right, []) if end == right else []
            if trip:
                connected, latched, trip_time = False, True, end
            for event in commands:
                if event.kind == 'source_voltage':
                    scale = event.value
                elif event.kind == 'load_torque_scale':
                    load_scale = event.value
                elif event.kind == 'motor_open':
                    connected = False
                elif not latched:
                    connected = True
            if trip or commands:
                changes.append(end)
            assert end > cursor or trip
            cursor = end
    # Compare pre/post limits on their respective reference segments. Small
    # root-time differences are measured separately, rather than appearing as
    # a false current mismatch across an instantaneous contactor operation.
    out = []
    for time, side in zip(report['time_s'], report['sample_side']):
        nearby = next((t for t in changes if abs(t-time) < 1e-6), None)
        at = time if nearby is None else nearby
        candidates = [segment for segment in segments if segment[0]-1e-12 <= at <= segment[1]+1e-12]
        segment = candidates[0] if side == 'pre_event' else candidates[-1]
        left, right, interpolant, connected, scale, _ = segment
        x = interpolant(min(right, max(left, at)))
        stator, _ = current(x)
        out.append([x[2], x[3], abs(stator)/ib, abs(source_v*scale-feed*stator)/vb])
    return {'trip_time_s': trip_time, 'values': np.asarray(out), 'event_alignment_tolerance_s': 1e-6}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for name in ('cold_trip', 'warm_trip', 'no_trip', 'blocked_reclose'):
        path = Path('cases/studies/thermal_'+name+'.json')
        spec = StudySpec.model_validate_json(path.read_text())
        result = run_study(spec)
        if not result.get('completed'):
            raise RuntimeError(result)
        expected = reference(spec, result)
        motor = spec.induction.motors[0]
        keys = ['speed_pu', 'thermal_level', 'current_pu', 'bus_voltage_pu']
        actual = np.column_stack([result['channels'][motor.id+'.'+key] for key in keys])
        errors = dict(zip(keys, np.max(abs(actual-expected['values']), axis=0).tolist()))
        actual_trip = result['motor_summary'][motor.id]['thermal']['trip_time_s']
        expected_trip = expected['trip_time_s']
        same_trip = (actual_trip is None) == (expected_trip is None)
        trip_error = abs(actual_trip-expected_trip) if actual_trip is not None and expected_trip is not None else None
        limits = dict(speed_pu=2e-5, thermal_level=2e-5, current_pu=1e-4, bus_voltage_pu=1e-5)
        passed = same_trip and (trip_error is None or trip_error <= 1e-6) and all(errors[k] < limits[k] for k in keys)
        write_json(args.out/(name+'.json'), result)
        write_json(args.out/(name+'-reference.json'), dict(time_s=result['time_s'], sample_side=result['sample_side'],
                   channels=dict(zip(keys, expected['values'].T.tolist())), trip_time_s=expected_trip))
        rows.append(dict(case=str(path), case_sha256=sha256_file(path), maximum_errors=errors, limits=limits,
                         trip_time_error_s=trip_error, trip_time_tolerance_s=1e-6, passed=passed))
    write_json(args.out/'reference.json', dict(cases=rows, reference_sha256=sha256_file(Path(__file__)),
               circuit_reference_sha256=sha256_file(Path('benchmarks/induction_reference.py')),
               method='Independent SI rotor-flux/thermal ODE, DOP853, terminal root and latched open circuit',
               field_validated=False, industrial_profile_qualified=False))
    print([(row['case'], row['passed'], row['trip_time_error_s']) for row in rows])
    if not all(row['passed'] for row in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
