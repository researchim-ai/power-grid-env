"""Recover actual terminals from native receiving powers and voltage magnitude.

The collapsed ANDES Slack is the internal source EMF. Its voltage cannot replace
voltage at the source terminal after the Thevenin impedance. This observer uses
only native port measurements, authored source inputs and circuit parameters.
"""
from __future__ import annotations
import math
from types import SimpleNamespace
import numpy as np
from benchmarks.andes_group_motor_reference import scope
from benchmarks.andes_joint_double_cage import validate_event_samples
from power_grid_env.model import canonical_hash

CLOSURE_LIMIT_PU = 1e-6


def reconstruct_terminals(spec, native, *, additional_event_times=()):
    scope(spec)
    if native.get('completed') is not True or native.get('backend_version') != '2.0.0':
        raise ValueError('completed pinned native trajectory required')
    if native.get('case_sha256') != canonical_hash(spec):
        raise ValueError('native measurement case identity mismatch')
    settings = spec.coupled if spec.coupled else spec.induction
    registered=set(e.time_s for e in settings.events)
    for at in additional_event_times:
        if not math.isfinite(at) or not 0<at<=settings.duration_s:raise ValueError('invalid additional native event time')
        registered.add(float(at))
    timeline=SimpleNamespace(duration_s=settings.duration_s,events=[SimpleNamespace(time_s=t) for t in sorted(registered)])
    validate_event_samples(native, timeline)
    times = np.asarray(native['time_s'], dtype=float)
    sides = native['sample_side']
    channels = native['channels']

    def column(key):
        if key not in channels:
            raise ValueError('missing native measurement: ' + key)
        values = np.asarray(channels[key], dtype=float)
        if values.shape != times.shape or not np.isfinite(values).all():
            raise ValueError('malformed native measurement: ' + key)
        return values

    motors = settings.motors
    receiving = column(motors[0].id + '.bus_voltage_pu')
    if np.any(receiving < 0):
        raise ValueError('negative voltage magnitude')
    for m in motors[1:]:
        if not np.allclose(column(m.id + '.bus_voltage_pu'), receiving, rtol=0, atol=1e-12):
            raise ValueError('native motor ports do not share a voltage')
    power = np.zeros(len(times), dtype=complex)
    for m in motors:
        power += column(m.id + '.p_w') + 1j * column(m.id + '.q_var')
    for g in spec.network.generators:
        power -= column(g.id + '.p_w') + 1j * column(g.id + '.q_var')

    n = spec.network
    source, line = n.sources[0], n.lines[0]
    zs = complex(source.r_ohm, source.x_ohm)
    zl = complex(line.r_ohm[0][0] - line.r_ohm[0][1], line.x_ohm[0][0] - line.x_ohm[0][1])
    voltage_base = motors[0].rated_ll_v / math.sqrt(3)
    source_events = sorted((e for e in settings.events if e.kind == 'source_voltage'), key=lambda e: e.time_s)
    scales = []
    for at, side in zip(times, sides):
        scale = 1.
        for e in source_events:
            if e.time_s < at or (e.time_s == at and side != 'pre_event'):
                scale = e.value
        scales.append(scale)
    emf = source.ll_v / math.sqrt(3) * np.array(scales) * np.exp(1j * source.angle_a_rad)
    magnitude = receiving * voltage_base
    vp = np.zeros(len(times), dtype=complex)
    current = np.zeros(len(times), dtype=complex)
    live = magnitude > 0
    # Work in the receiving-voltage frame first. Rotation fixes phase only;
    # no voltage/current gain is fitted to enforce the driving-source input.
    vp[live] = magnitude[live]
    current[live] = np.conj(power[live]) / (3 * magnitude[live])
    calculated_emf = vp + (zs + zl) * current
    rotation = np.exp(1j * (np.angle(emf[live]) - np.angle(calculated_emf[live])))
    vp[live] *= rotation
    current[live] *= rotation
    dead = ~live
    if np.any(dead):
        if np.any(abs(power[dead]) > 1e-8) or abs(zs + zl) == 0:
            raise ValueError('zero-voltage port is inconsistent or needs current phasors')
        # At a zero receiving voltage, the source/branch equation still fixes
        # branch current when the total series impedance is nonzero.
        current[dead] = emf[dead] / (zs + zl)
    calculated_emf = vp + (zs + zl) * current
    closure = abs(calculated_emf - emf) / voltage_base
    if not np.isfinite(closure).all() or np.max(closure) > CLOSURE_LIMIT_PU:
        raise ValueError('native port power/voltage violates source closure')
    terminal = vp + zl * current
    source_power = 3 * terminal * np.conj(current)
    ideal_power = 3 * emf * np.conj(current)
    terminals = {t.id: t.connectivity_node_id for t in n.terminals}
    return dict(time_s=native['time_s'], sample_side=sides,
                node_voltage_pu={terminals[source.terminal_id]: (abs(terminal)/voltage_base).tolist(),
                                 motors[0].node_id: receiving.tolist()},
                channels={'source.terminal_voltage_pu': (abs(terminal)/voltage_base).tolist(),
                          'source.internal_emf_voltage_pu': (abs(emf)/voltage_base).tolist(),
                          'source.branch_current_a': abs(current).tolist(),
                          'source.p_w': source_power.real.tolist(), 'source.q_var': source_power.imag.tolist(),
                          'source.internal_p_w': ideal_power.real.tolist(), 'source.internal_q_var': ideal_power.imag.tolist()},
                maximum_source_closure_pu=float(np.max(closure)), source_closure_limit_pu=CLOSURE_LIMIT_PU,
                source_voltage_is_actual_terminal=True, production_measurements_used=False,
                native_closed_loop_controller_qualified=False, field_validated=False, industrial_profile_qualified=False)
