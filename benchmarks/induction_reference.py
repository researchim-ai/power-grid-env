"""Independent SI rotor-flux integration and full equivalent-circuit checks.

For declared common-bus motor groups behind one source and one line only. This reference solves
stator and rotor currents simultaneously from physical inductances, without
calling production network stamps, model constants or the voltage-state ODE.
"""
from pathlib import Path
import argparse
import cmath
import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq, root
from power_grid_env.report import write_json, sha256_file
from power_grid_env.study_spec import StudySpec, run_study


def circuit_parameters(spec, motor=None):
    n, m = spec.network, motor or spec.induction.motors[0]
    assert len(n.lines) == len(n.sources) == 1
    assert not (n.loads or n.transformers or n.generators or n.shunts)
    line = n.lines[0]
    zline = complex(line.r_ohm[0][0]-line.r_ohm[0][1], line.x_ohm[0][0]-line.x_ohm[0][1])
    assert not np.any(line.b_siemens)
    source = n.sources[0]
    zs = complex(source.r_ohm, source.x_ohm)
    wb = 2*math.pi*n.frequency_hz
    zb = m.rated_ll_v**2/m.rated_va
    rs, rr = m.stator_r_pu*zb, m.rotor_r_pu*zb
    lm = m.magnetizing_x_pu*zb/wb
    ls = (m.stator_x_pu+m.magnetizing_x_pu)*zb/wb
    lr = (m.rotor_x_pu+m.magnetizing_x_pu)*zb/wb
    source_v = source.ll_v/math.sqrt(3)*cmath.exp(1j*source.angle_a_rad)
    return m, wb, rs, rr, lm, ls, lr, zs+zline, source_v


def equivalent_circuit(spec, slip, source_scale=1.):
    """Independent steady per-phase full T circuit: stator / Xm / rotor R/s."""
    m, wb, rs, rr, lm, ls, lr, feed, source_v = circuit_parameters(spec)
    rotor = complex(rr/slip, wb*(lr-lm)) if slip else complex(np.inf, 0)
    magnetizing = 1j*wb*lm
    parallel = magnetizing if slip == 0 else 1/(1/magnetizing+1/rotor)
    impedance = complex(rs, wb*(ls-lm))+parallel
    current = source_v*source_scale/(feed+impedance)
    voltage = current*impedance
    rotor_current = 0j if slip == 0 else current*parallel/rotor
    torque = 0. if slip == 0 else 3*abs(rotor_current)**2*rr/slip/m.rated_va
    return voltage, current, torque


def mechanical_torque_reference(motor, speed):
    """Declared load law, evaluated independently without production methods."""
    if motor.mechanical_load is None:
        return motor.fan_torque_pu*speed*abs(speed)
    law = motor.mechanical_load
    torque = law.viscous_torque_pu*speed+law.quadratic_torque_pu*speed*abs(speed)
    if law.friction_torque_pu:
        torque += law.friction_torque_pu*math.tanh(speed/law.smoothing_speed_pu)
    return torque


def initial_flux_reference(spec):
    """Solve the full physical T circuit and shaft balance, not voltage-state RHS."""
    motors = spec.induction.motors
    count = len(motors)
    initial = np.zeros((count, 3))
    if spec.induction.operating_point is None:
        return initial.ravel()
    active = [i for i, m in enumerate(motors) if m.connected]
    parameters = [circuit_parameters(spec, m) for m in motors]
    wb, feed, source = parameters[0][1], parameters[0][7], parameters[0][8]
    def circuit(values):
        speed = np.zeros(count); speed[active] = values
        z, parallel, rotor_z = [], [], []
        for i, p in enumerate(parameters):
            _, _, rs, rr, lm, ls, lr, _, _ = p
            slip = 1-speed[i]
            rotor = complex(rr/slip, wb*(lr-lm)) if abs(slip) > 1e-14 else complex(np.inf, 0.)
            magnetizing = 1j*wb*lm
            branch = 1/(1/magnetizing+1/rotor)
            parallel.append(branch); rotor_z.append(rotor)
            z.append(complex(rs, wb*(ls-lm))+branch)
        admittance = np.array([1/z[i] if m.connected else 0j for i, m in enumerate(motors)])
        voltage = source/(1+feed*sum(admittance))
        current = voltage*admittance
        rotor = np.array([current[i]*parallel[i]/rotor_z[i] for i in range(count)])
        torque = np.array([0. if abs(1-speed[i]) < 1e-14 else 3*abs(rotor[i])**2*parameters[i][3]/(1-speed[i])/motors[i].rated_va for i in range(count)])
        flux = np.array([parameters[i][4]*current[i]-parameters[i][6]*rotor[i] for i in range(count)])
        return speed, flux, torque
    def error(values):
        speed, _, torque = circuit(values)
        return np.array([torque[i]-mechanical_torque_reference(motors[i], speed[i]) for i in active])
    minimum = spec.induction.operating_point.minimum_speed_pu
    if len(active) == 1:
        grid = np.linspace(minimum, 1., 401)
        roots = []
        for lo, hi in zip(grid, grid[1:]):
            if error([lo])[0]*error([hi])[0] <= 0:
                roots.append(brentq(lambda w: error([w])[0], lo, hi, xtol=1e-13))
        assert roots, 'no independent high-speed root for this fixture'
        values = [max(roots)]
    else:
        solution = root(error, np.full(len(active), .99), tol=1e-11)
        assert solution.success and max(abs(error(solution.x))) < 1e-8
        values = solution.x
    speed, flux, _ = circuit(values)
    assert all(minimum <= speed[i] <= 1. for i in active)
    return np.column_stack([flux.real, flux.imag, speed]).ravel()


def rotor_flux_group_reference(spec, report):
    motors = spec.induction.motors
    assert len({m.node_id for m in motors}) == 1, 'common receiving bus fixture required'
    parameters = [circuit_parameters(spec, m) for m in motors]
    count = len(motors)
    wb, feed, source_v = parameters[0][1], parameters[0][7], parameters[0][8]
    rs, rr, lm, ls, lr = [np.array([p[k] for p in parameters]) for k in range(2, 7)]
    ratings = np.array([m.rated_va for m in motors])
    vbase = np.array([m.rated_ll_v/math.sqrt(3) for m in motors])
    ibase = ratings/(3*vbase)
    load_scale = np.ones(count)
    inertia = np.array([2*m.inertia_s for m in motors])
    connected, scale = np.array([m.connected for m in motors]), 1.
    matrix = None
    def rebuild():
        nonlocal matrix
        matrix = np.zeros((2*count, 2*count), dtype=complex)
        for i in range(count):
            if connected[i]:
                matrix[i, :count] = feed*connected
                matrix[i, i] += rs[i]+1j*wb*ls[i]
                matrix[i, i+count] = 1j*wb*lm[i]
            else:
                matrix[i, i] = 1.
            matrix[count+i, i] = lm[i]
            matrix[count+i, count+i] = lr[i]
    def algebraic(flux):
        solved = np.linalg.solve(matrix, np.r_[source_v*scale*connected, flux])
        stator, rotor = solved[:count], solved[count:]
        voltage = source_v*scale-feed*sum(stator)
        torque = -3*wb*lm*(rotor*np.conj(stator)).imag/ratings
        return stator, rotor, voltage, torque
    def rhs(time, x):
        x = x.reshape(-1, 3)
        flux = x[:, 0]+1j*x[:, 1]
        _, rotor, _, torque = algebraic(flux)
        dflux = -rr*rotor-1j*wb*(1-x[:, 2])*flux
        load = np.array([mechanical_torque_reference(m, w) for m, w in zip(motors, x[:, 2])])*load_scale
        dw = (torque-load)/inertia
        return np.column_stack([dflux.real, dflux.imag, dw]).ravel()
    event_map = {}
    for event in spec.induction.events:
        event_map.setdefault(event.time_s, []).append(event)
    boundaries = [0., *sorted(event_map), spec.induction.duration_s]
    times, sides = np.array(report['time_s']), report['sample_side']
    out = {m.id: np.zeros((len(times), 7)) for m in motors}
    state = initial_flux_reference(spec)
    for left, right in zip(boundaries, boundaries[1:]):
        rebuild()
        sol = solve_ivp(rhs, (left, right), state, method='DOP853', rtol=2e-11, atol=1e-12,
                        max_step=min(.001, spec.induction.step_s/2), dense_output=True)
        assert sol.success, sol.message
        selected = ((times > left) & (times < right)) | ((times == left) & np.array([s != 'pre_event' for s in sides])) | ((times == right) & np.array([s != 'post_event' for s in sides]))
        for i in np.flatnonzero(selected):
            x = sol.sol(times[i]).reshape(-1, 3); flux = x[:, 0]+1j*x[:, 1]
            current, _, voltage, _ = algebraic(flux)
            e = 1j*wb*lm/lr*flux/vbase
            power = 3*voltage*np.conj(current)
            for j, motor in enumerate(motors):
                out[motor.id][i] = [x[j, 2], e[j].real, e[j].imag, abs(current[j])/ibase[j],
                                   abs(voltage)/vbase[j], power[j].real, power[j].imag]
        state = sol.y[:, -1]
        for event in event_map.get(right, []):
            if event.kind == 'source_voltage':
                scale = event.value
            elif event.kind == 'load_torque_scale':
                load_scale[next(i for i, m in enumerate(motors) if m.id == event.equipment_id)] = event.value
            else:
                connected[next(i for i, m in enumerate(motors) if m.id == event.equipment_id)] = event.kind == 'motor_close'
    return out


def rotor_flux_reference(spec, report):
    assert len(spec.induction.motors) == 1
    return rotor_flux_group_reference(spec, report)[spec.induction.motors[0].id]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for name in ('start', 'sag_restart', 'contactor_restart', 'group_simultaneous', 'group_staggered'):
        path = Path(f'cases/studies/induction_{name}.json')
        spec = StudySpec.model_validate_json(path.read_text())
        actual = run_study(spec)
        if not actual.get('completed'):
            raise RuntimeError(actual)
        references = rotor_flux_group_reference(spec, actual)
        keys = ['speed_pu', 'e_real_pu', 'e_imag_pu', 'current_pu', 'bus_voltage_pu', 'p_w', 'q_var']
        limits = dict(zip(keys, [2e-5, 2e-5, 2e-5, 1e-4, 1e-5, 100., 100.]))
        errors = {}
        for motor in spec.induction.motors:
            columns = np.column_stack([actual['channels'][motor.id+'.'+key] for key in keys])
            errors[motor.id] = dict(zip(keys, np.max(abs(columns-references[motor.id]), axis=0).tolist()))
        passed = all(row[key] <= limits[key] for row in errors.values() for key in keys)
        write_json(args.out/(name+'.json'), actual)
        write_json(args.out/(name+'-reference.json'), dict(time_s=actual['time_s'], sample_side=actual['sample_side'],
                   channels={motor: dict(zip(keys, values.T.tolist())) for motor, values in references.items()}))
        rows.append(dict(case=str(path), case_sha256=sha256_file(path), maximum_errors=errors, limits=limits, passed=passed))
    write_json(args.out/'reference.json', dict(cases=rows, reference_sha256=sha256_file(Path(__file__)),
               method='Independent SI rotor-flux ODE; physical coupled stator/rotor circuit; DOP853',
               field_validated=False, industrial_profile_qualified=False))
    print([(row['case'], row['passed'], max(v['speed_pu'] for v in row['maximum_errors'].values())) for row in rows])
    if not all(row['passed'] for row in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
