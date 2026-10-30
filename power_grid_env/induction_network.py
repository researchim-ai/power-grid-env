"""Single-cage rotor dynamics coupled to an exact linear positive-sequence network.

States per motor: complex transient internal voltage E and speed omega.
I=(V-E)/(rs+j*x'); E'=-(1/T+j*wb*(1-omega))*E+j*(X-X')/T*I.
2H*omega'=Re(E*conj(I))-Tfan*omega*abs(omega).
"""
from __future__ import annotations

import math
import numpy as np
from scipy.integrate import solve_ivp
from scipy.sparse import csc_matrix, lil_matrix
from scipy.sparse.linalg import splu

from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash, validate_network_rules
from power_grid_env.rms_study import positive_sequence, transformer_parameters
from power_grid_env.topology import compile_topology


def validate_induction(network, settings, *, validate_rules=True):
    topology = compile_topology(network)
    errors = [*(validate_network_rules(network) if validate_rules else []), *topology.errors]
    if len(topology.islands) != 1 or len(network.sources) != 1 or not network.sources[0].is_slack:
        errors.append('one connected island and one external slack required')
    if any((network.generators, network.inverters, network.storages, network.shunts,
            network.neutral_grounds, network.study_faults, network.harmonics, network.omissions)):
        errors.append('generators/DER/shunts/neutral/faults/harmonics/omissions unsupported')
    if not math.isfinite(network.s_base_va) or network.s_base_va <= 0 or not math.isfinite(network.frequency_hz) or network.frequency_hz <= 0:
        errors.append('positive finite system MVA and frequency required')
    if len(topology.buses) > 2000:
        errors.append('motor network exceeds 2000-bus implementation budget')
    samples = math.ceil(settings.duration_s / settings.step_s) + 2 * (len(settings.events) + sum(m.thermal is not None for m in settings.motors)) + 2
    if samples * (2 * len(topology.buses) + 14 * len(settings.motors) + 6) > 2_000_000:
        errors.append('motor trace exceeds two-million-scalar budget')
    if any(set(n.phases) != set('ABC') for n in network.connectivity_nodes):
        errors.append('only ABC nodes supported')
    if any(s.representation != 'topological' or set(s.phases) != set('ABC') for s in network.switches):
        errors.append('only fixed ABC topological switches supported')
    for source in network.sources:
        if source.phase_pu is not None or source.participation is not None or not all(math.isfinite(v) for v in (source.r_ohm, source.x_ohm, source.ll_v, source.angle_a_rad)) or source.r_ohm < 0 or source.x_ohm <= 0:
            errors.append(f'{source.id}: explicit balanced inductive source required')
    for line in network.lines:
        try:
            left = topology.buses[topology.terminal_bus[line.terminal_from]]
            right = topology.buses[topology.terminal_bus[line.terminal_to]]
            z = positive_sequence(np.array(line.r_ohm) + 1j*np.array(line.x_ohm))
            positive_sequence(line.b_siemens)
            if list(line.phases) != list('ABC') or left.nominal_ll_v != right.nominal_ll_v or z.real < -1e-12 or z.imag <= 0:
                raise ValueError('balanced passive inductive same-voltage line required')
        except (ValueError, KeyError) as exc:
            errors.append(f'{line.id}: {exc}')
    for transformer in network.transformers:
        try:
            transformer_parameters(transformer, topology, network.s_base_va)
        except (ValueError, KeyError) as exc:
            errors.append(f'{transformer.id}: {exc}')
    for load in network.loads:
        if set(load.phases) != set('ABC') or load.model != 'constant_z':
            errors.append(f'{load.id}: only balanced constant_z background loads supported')
        for values in (load.p_w, load.q_var):
            if set(values) != set('ABC') or not np.isfinite(list(values.values())).all() or not np.allclose(list(values.values()), next(iter(values.values()), 0), rtol=1e-10, atol=1e-6):
                errors.append(f'{load.id}: unequal/nonfinite phase powers')
        if load.opendss_model not in (None, 2):
            errors.append(f'{load.id}: conflicting phase-backend load model')
        if load.cold_load_multiplier is not None or load.cold_load_tau_s is not None:
            errors.append(f'{load.id}: cold-load dynamics unsupported')
        if load.rated_kv is not None:
            # rated_kv has connection-dependent phase/line conventions in the
            # phase backend; this first motor adapter requires the bus base.
            errors.append(f'{load.id}: explicit load rated_kv not supported; use bus-base powers')
    all_ids = {x.id for group in (network.sources, network.loads, network.generators, network.lines, network.transformers, network.switches) for x in group}
    for motor in settings.motors:
        constants = motor.constants(network.frequency_hz) if network.frequency_hz > 0 else (float('nan'),)
        factor = motor.rated_va / network.s_base_va if network.s_base_va > 0 else float('nan')
        if not all(math.isfinite(c) and c > 0 for c in (*constants, factor)):
            errors.append(f'{motor.id}: motor base/constants not numerically representable')
        if motor.id in all_ids:
            errors.append(f'{motor.id}: motor must not duplicate an existing device/load')
        if motor.node_id not in topology.node_to_bus:
            errors.append(f'{motor.id}: unknown node')
        else:
            nominal = topology.buses[topology.node_to_bus[motor.node_id]].nominal_ll_v
            if not math.isclose(motor.rated_ll_v, nominal, rel_tol=1e-9):
                errors.append(f'{motor.id}: motor rated voltage must equal bus base')
    states = {m.id: m.connected for m in settings.motors}
    for event in sorted(settings.events, key=lambda e: e.time_s):
        if event.kind == 'source_voltage':
            if event.equipment_id not in {s.id for s in network.sources}:
                errors.append('unknown source voltage target')
        elif event.equipment_id not in states:
            errors.append('unknown motor target')
        elif event.kind == 'load_torque_scale':
            pass
        else:
            closed = event.kind == 'motor_close'
            if states[event.equipment_id] == closed:
                errors.append('redundant contactor operation')
            states[event.equipment_id] = closed
    return {'ok': not errors, 'errors': errors, 'model': 'single_cage_third_order_balanced',
            'limitations': ['algebraic stator; no EMT/inrush waveform', 'constant_z background only',
                            'fan/viscous/quadratic/smooth friction; no hard stiction or gravity load', 'no saturation/deep-bar/VFD or winding/rotor temperature qualification',
                            'no synchronous generators or islanding', 'no field validation']}


class InductionNetwork:
    """Factor once per contactor segment; preserve motor flux while disconnected."""
    def __init__(self, network, settings):
        self.network, self.settings = network, settings
        self.topology = topology = compile_topology(network)
        self.bus_names = sorted(topology.buses)
        index = {bus: i for i, bus in enumerate(self.bus_names)}
        self.motor_buses = np.array([index[topology.node_to_bus[m.node_id]] for m in settings.motors])
        self.factors = np.array([m.rated_va/network.s_base_va for m in settings.motors])
        self.constants = [m.constants(network.frequency_hz) for m in settings.motors]
        self.zmotor = np.array([complex(m.stator_r_pu, c[0]) for m, c in zip(settings.motors, self.constants)])
        self.base_y = lil_matrix((len(index), len(index)), dtype=complex)
        def branch(left, right, z, shunt=0j, tap=1+0j):
            a, b = index[left], index[right]
            y = 1/z
            self.base_y[a, a] += (y+shunt/2)/abs(tap)**2
            self.base_y[b, b] += y+shunt/2
            self.base_y[a, b] -= y/np.conj(tap)
            self.base_y[b, a] -= y/tap
        for line in network.lines:
            a, b = topology.terminal_bus[line.terminal_from], topology.terminal_bus[line.terminal_to]
            zb = topology.buses[a].nominal_ll_v**2/network.s_base_va
            z = positive_sequence(np.array(line.r_ohm)+1j*np.array(line.x_ohm))/zb
            shunt = 1j*positive_sequence(line.b_siemens).real*zb
            branch(a, b, z, shunt)
        for transformer in network.transformers:
            p = transformer_parameters(transformer, topology, network.s_base_va)
            a, b = [topology.terminal_bus[w.terminal_id] for w in transformer.windings]
            branch(a, b, complex(p['r'], p['x']), tap=p['tap']*np.exp(1j*p['phi']))
        for load in network.loads:
            bus = index[topology.terminal_bus[load.terminal_id]]
            self.base_y[bus, bus] += complex(sum(load.p_w.values()), -sum(load.q_var.values()))/network.s_base_va
        source = network.sources[0]
        self.source_bus = index[topology.terminal_bus[source.terminal_id]]
        nominal = topology.buses[self.bus_names[self.source_bus]].nominal_ll_v
        self.source_e = source.ll_v/nominal*np.exp(1j*source.angle_a_rad)
        self.source_y = (nominal**2/network.s_base_va)/complex(source.r_ohm, source.x_ohm)
        self.base_y[self.source_bus, self.source_bus] += self.source_y
        self.base_y = csc_matrix(self.base_y)
        self.connected = np.array([m.connected for m in settings.motors])
        self.source_scale = 1.
        self.load_scale = np.ones(len(settings.motors))
        self.electrical_size = 3*len(settings.motors)
        self.thermal_indices = {i: self.electrical_size+j for j, i in enumerate(
            i for i, m in enumerate(settings.motors) if m.thermal is not None)}
        self.thermal_latched = np.zeros(len(settings.motors), dtype=bool)
        self.factorize()

    def factorize(self):
        matrix = self.base_y.tolil(copy=True)
        self.motor_y = np.where(self.connected, self.factors/self.zmotor, 0j)
        for bus, y in zip(self.motor_buses, self.motor_y):
            matrix[bus, bus] += y
        self.matrix = csc_matrix(matrix)
        self.lu = splu(self.matrix)

    def electrical(self, state):
        values = np.asarray(state)[:self.electrical_size].reshape(-1, 3)
        e = values[:, 0]+1j*values[:, 1]
        rhs = np.zeros(len(self.bus_names), dtype=complex)
        rhs[self.source_bus] = self.source_y*self.source_e*self.source_scale
        np.add.at(rhs, self.motor_buses, self.motor_y*e)
        voltage = self.lu.solve(rhs)
        current = np.where(self.connected, (voltage[self.motor_buses]-e)/self.zmotor, 0j)
        torque = (e*np.conj(current)).real
        mismatch = float(np.max(abs(self.matrix@voltage-rhs)))
        return voltage, current, torque, mismatch

    def rhs(self, time, state):
        _, current, torque, _ = self.electrical(state)
        return self.motor_derivatives(state, current, torque)

    def motor_derivatives(self, state, current, torque):
        values = state[:self.electrical_size].reshape(-1, 3)
        e = values[:, 0]+1j*values[:, 1]
        speed = values[:, 2]
        xdiff = np.array([c[1] for c in self.constants])
        tau = np.array([c[2] for c in self.constants])
        de = -(1/tau+1j*2*math.pi*self.network.frequency_hz*(1-speed))*e+1j*xdiff/tau*current
        load = np.array([m.load_torque(w) for m, w in zip(self.settings.motors, speed)])*self.load_scale
        dw = (torque-load)/np.array([2*m.inertia_s for m in self.settings.motors])
        derivatives = np.column_stack([de.real, de.imag, dw]).ravel()
        heating = [(abs(current[i])**2-state[address])/self.settings.motors[i].thermal.time_constant_s
                   for i, address in self.thermal_indices.items()]
        return np.r_[derivatives, heating]

    def steady_electrical(self, speed):
        """Eliminate rotor states analytically at a prescribed vector of slips."""
        wb = 2*math.pi*self.network.frequency_hz
        response = np.array([1j*c[1]/(1+1j*wb*c[2]*(1-w)) for c, w in zip(self.constants, speed)])
        equivalent = self.zmotor+response
        admittance = np.where(self.connected, self.factors/equivalent, 0j)
        matrix = self.base_y.tolil(copy=True)
        for bus, y in zip(self.motor_buses, admittance):
            matrix[bus, bus] += y
        rhs = np.zeros(len(self.bus_names), dtype=complex)
        rhs[self.source_bus] = self.source_y*self.source_e*self.source_scale
        voltage = splu(csc_matrix(matrix)).solve(rhs)
        current = np.where(self.connected, voltage[self.motor_buses]/equivalent, 0j)
        internal = response*current
        torque = (internal*np.conj(current)).real
        return voltage, current, internal, torque

    def additional_channels(self):
        return {}

    def additional_signals(self, state, voltage):
        return {}, 0j

    def apply_additional_event(self, event):
        return None

    def additional_metadata(self):
        return {}

    def validate_additional_channels(self, channels):
        return {'ok': True}

    def observe_accepted_states(self, states):
        pass

    def thermal_guards(self):
        indices, guards = [], []
        for i, address in self.thermal_indices.items():
            if self.thermal_latched[i]:
                continue
            threshold = self.settings.motors[i].thermal.trip_level
            def crossing(time, state, address=address, threshold=threshold):
                return state[address]-threshold
            crossing.terminal = True
            crossing.direction = 1
            indices.append(i)
            guards.append(crossing)
        return indices, guards


def run_induction(network, settings):
    scope = validate_induction(network, settings)
    if not scope['ok']:
        return {'solved': False, 'completed': False, 'status': 'rejected', 'reason': 'induction_scope', 'scope': scope}
    try:
        model = InductionNetwork(network, settings)
    except (ValueError, RuntimeError, ArithmeticError) as exc:
        return {'solved': False, 'completed': False, 'status': 'network_factorization_failed',
                'error': str(exc), 'scope': scope}
    from power_grid_env.motor_operating_point import initialize_motor_state
    state, operating_point = initialize_motor_state(model)
    if state is None:
        return {'solved': False, 'completed': False, 'status': 'initialization_failed',
                'reason': operating_point['reason'], 'operating_point': operating_point, 'scope': scope}
    return integrate_induction_model(model, scope, state, operating_point)


def integrate_induction_model(model, scope, state, operating_point, *, study='motor_start'):
    """Shared event/thermal integrator; extra machines use the same nodal solve."""
    network, settings = model.network, model.settings
    events = {}
    for event in settings.events:
        events.setdefault(event.time_s, []).append(event)
    boundaries = [0., *sorted(events), settings.duration_s]
    regular = np.arange(math.ceil(settings.duration_s/settings.step_s))*settings.step_s
    channels = {f'{m.id}.{key}': [] for m in settings.motors for key in (
        'speed_pu', 'slip', 'e_real_pu', 'e_imag_pu', 'current_pu', 'bus_voltage_pu', 'p_w', 'q_var', 'te_pu', 'load_torque_pu', 'load_scale', 'connected')}
    channels.update({f'{bus}.{key}': [] for bus in model.bus_names for key in ('v', 'a')})
    for i in model.thermal_indices:
        for key in ('thermal_level', 'thermal_latched'):
            channels[f'{settings.motors[i].id}.{key}'] = []
    channels.update(model.additional_channels())
    times, sides, kcl = [], [], []
    event_log = []
    source_power, power_balance = [], []
    def record(time, values, side):
        voltage, current, torque, mismatch = model.electrical(values)
        full_state = values
        values = values[:model.electrical_size].reshape(-1, 3)
        times.append(float(time)); sides.append(side); kcl.append(mismatch)
        for i, motor in enumerate(settings.motors):
            speed = values[i, 2]
            s = voltage[model.motor_buses[i]]*np.conj(current[i])*motor.rated_va
            row = dict(speed_pu=speed, slip=1-speed, e_real_pu=values[i, 0], e_imag_pu=values[i, 1],
                       current_pu=abs(current[i]), bus_voltage_pu=abs(voltage[model.motor_buses[i]]), p_w=s.real, q_var=s.imag, te_pu=torque[i],
                       load_torque_pu=motor.load_torque(speed)*model.load_scale[i], load_scale=model.load_scale[i], connected=bool(model.connected[i]))
            for key, value in row.items():
                channels[f'{motor.id}.{key}'].append(float(value))
            if i in model.thermal_indices:
                channels[f'{motor.id}.thermal_level'].append(float(full_state[model.thermal_indices[i]]))
                channels[f'{motor.id}.thermal_latched'].append(float(model.thermal_latched[i]))
        for i, bus in enumerate(model.bus_names):
            channels[f'{bus}.v'].append(float(abs(voltage[i])))
            channels[f'{bus}.a'].append(float(np.angle(voltage[i])))
        additional, additional_injection = model.additional_signals(full_state, voltage)
        for key, value in additional.items():
            channels[key].append(float(value))
        source_i = model.source_y*(model.source_e*model.source_scale-voltage[model.source_bus])
        injected = voltage[model.source_bus]*np.conj(source_i)*network.s_base_va
        source_power.append(injected)
        passive_i = model.base_y@voltage
        passive_i[model.source_bus] -= model.source_y*voltage[model.source_bus]
        passive_power = np.sum(voltage*np.conj(passive_i))*network.s_base_va
        motor_power = np.sum(voltage[model.motor_buses]*np.conj(current)*model.factors)*network.s_base_va
        power_balance.append(float(abs(injected+additional_injection-passive_power-motor_power)/network.s_base_va))
    record(0., state, 'initial')
    nfev = 0
    for left, right in zip(boundaries, boundaries[1:]):
        cursor = left
        while cursor < right:
            guard_indices, guards = model.thermal_guards()
            try:
                solution = solve_ivp(model.rhs, (cursor, right), state, method='Radau',
                                     rtol=settings.rtol, atol=settings.atol, max_step=settings.step_s,
                                     dense_output=True, events=guards or None)
            except (ValueError, RuntimeError, ArithmeticError) as exc:
                return {'solved': True, 'completed': False, 'status': 'numerically_not_converged',
                        'solver_message': str(exc), 'scope': scope}
            nfev += solution.nfev
            if not solution.success or not np.isfinite(solution.y).all():
                return {'solved': True, 'completed': False, 'status': 'numerically_not_converged',
                        'solver_message': solution.message, 'scope': scope}
            model.observe_accepted_states(solution.y)
            stop = float(solution.t[-1])
            state = solution.y[:, -1]
            triggered = {i for i, roots in zip(guard_indices, solution.t_events or []) if len(roots)}
            if triggered:
                # Identical relays can reach the same threshold in one root.
                # Apply exact numerical ties atomically; do not trip merely
                # nearby relays within the much wider ODE tolerance.
                _, pre_current, _, _ = model.electrical(state)
                for i in guard_indices:
                    passport = settings.motors[i].thermal
                    epsilon = 64*np.finfo(float).eps*max(abs(state[model.thermal_indices[i]]), passport.trip_level, np.finfo(float).tiny)
                    if (model.connected[i] and state[model.thermal_indices[i]] >= passport.trip_level-epsilon
                            and abs(pre_current[i])**2 > state[model.thermal_indices[i]]):
                        triggered.add(i)
            scheduled = events.get(right, []) if stop == right else []
            sample_times = [*regular[(regular > cursor) & (regular < stop)], stop]
            sampled = solution.sol(sample_times)
            sampled[:, -1] = state
            for time, values in zip(sample_times, sampled.T):
                record(time, values, 'pre_event' if time == stop and (triggered or scheduled) else 'regular')
            for i in sorted(triggered):
                motor = settings.motors[i]
                model.thermal_latched[i] = True
                model.connected[i] = False
                event_log.append({'time_s': stop, 'kind': 'thermal_trip', 'equipment_id': motor.id,
                                  'level': float(state[model.thermal_indices[i]]),
                                  'threshold': motor.thermal.trip_level, 'outcome': 'latched_open'})
            for event in scheduled:
                additional = model.apply_additional_event(event)
                if additional is not None:
                    event_log.append({'time_s':stop,'kind':event.kind,'equipment_id':event.equipment_id,**additional})
                    continue
                outcome = 'applied'
                if event.kind == 'source_voltage':
                    model.source_scale = event.value
                else:
                    i = next(i for i, m in enumerate(settings.motors) if m.id == event.equipment_id)
                    if event.kind == 'load_torque_scale':
                        model.load_scale[i] = event.value
                        event_log.append({'time_s': stop, 'kind': event.kind, 'equipment_id': event.equipment_id, 'value': event.value, 'outcome': 'applied'})
                        continue
                    requested = event.kind == 'motor_close'
                    if requested and model.thermal_latched[i]:
                        outcome = 'blocked_thermal_latch'
                    elif model.connected[i] == requested:
                        outcome = 'already_in_requested_state'
                    else:
                        model.connected[i] = requested
                event_log.append({'time_s': stop, 'kind': event.kind, 'equipment_id': event.equipment_id,
                                  'outcome': outcome})
            if triggered or scheduled:
                model.factorize()
                record(stop, state, 'post_event')
            if stop < cursor or (stop == cursor and not triggered):
                return {'solved': True, 'completed': False, 'status': 'integration_no_progress', 'scope': scope}
            cursor = stop
    channels[network.sources[0].id+'.p_w'] = [float(s.real) for s in source_power]
    channels[network.sources[0].id+'.q_var'] = [float(s.imag) for s in source_power]
    max_kcl = max(kcl)
    finite = all(np.isfinite(values).all() for values in channels.values())
    if not finite or not math.isfinite(max_kcl) or not all(math.isfinite(x) for x in power_balance):
        return {'solved': True, 'completed': False, 'status': 'invalid_numerical_result', 'scope': scope}
    additional_validation = model.validate_additional_channels(channels)
    complete = max_kcl <= 1e-8 and max(power_balance) <= 1e-8 and additional_validation['ok']
    summary = {}
    for motor_index, motor in enumerate(settings.motors):
        speed = np.asarray(channels[f'{motor.id}.speed_pu'])
        current = np.asarray(channels[f'{motor.id}.current_pu'])
        # A descriptive horizon result, not an asymptotic stall certificate.
        summary[motor.id] = {'final_speed_pu': float(speed[-1]), 'sampled_peak_current_pu': float(max(current)),
                             'minimum_sampled_bus_voltage_pu': float(min(channels[f'{motor.id}.bus_voltage_pu'])),
                             'final_speed_at_least_0_95': bool(speed[-1] >= .95),
                             'connected_at_end': bool(model.connected[motor_index])}
        if motor.thermal:
            summary[motor.id]['thermal'] = {
                'model': motor.thermal.model, 'trip_level': motor.thermal.trip_level,
                'final_level': channels[f'{motor.id}.thermal_level'][-1],
                'latched': bool(model.thermal_latched[motor_index]),
                'trip_time_s': next((e['time_s'] for e in event_log if e['kind'] == 'thermal_trip' and e['equipment_id'] == motor.id), None)}
    import scipy
    result = {'solved': True, 'completed': complete, 'status': 'completed' if complete else 'invalid_numerical_result',
            'study': study, 'backend': 'scipy', 'backend_version': scipy.__version__,
            'code_sha256': package_fingerprint(), 'network_sha256': canonical_hash(network),
            'settings': settings.model_dump(mode='json'), 'scope': scope, 'time_s': times,
            'sample_side': sides, 'channels': channels, 'motor_summary': summary, 'event_log': event_log,
            'mapping': {'motor_buses': {m.id: model.bus_names[int(bus)] for m, bus in zip(settings.motors, model.motor_buses)}},
            'maximum_kcl_residual_pu': max_kcl, 'maximum_power_balance_residual_pu': max(power_balance), 'integration': {'method': 'Radau', 'nfev': nfev},
            'initialization': 'loaded high-speed equilibrium' if settings.operating_point else 'zero rotor flux and zero speed; explicit initial contactor states',
            'operating_point': operating_point, 'additional_validation': additional_validation,
            'units': {'e_real_pu': 'pu bus voltage', 'e_imag_pu': 'pu bus voltage',
                      'speed_pu': 'pu synchronous mechanical speed', 'current_pu': 'pu motor rated current', 'bus_voltage_pu': 'pu motor rated line voltage',
                      'te_pu': 'pu motor rated VA / synchronous mechanical speed',
                      'load_torque_pu': 'pu motor rated VA / synchronous mechanical speed',
                      'thermal_level': 'dimensionless current-squared replica; rated-current equilibrium = 1',
                      'thermal_latched': '1 = latched trip; 0 = not tripped', 'v': 'pu bus voltage', 'a': 'rad', 'p_w': 'W', 'q_var': 'var'},
            'industrial_profile_qualified': False, 'field_validated': False}
    result.update(model.additional_metadata())
    return result
