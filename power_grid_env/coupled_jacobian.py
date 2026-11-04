"""Exact eliminated-network Jacobian for fixed-field GENCLS and cage motors.

Continuous derivatives inside one discrete topology only; no event saltation,
GENROU, governor or excitation-control derivative is claimed here.
"""
import math
from types import SimpleNamespace
import numpy as np
from power_grid_env.motor_jacobian import jacobian as motor_jacobian

KIND = 'analytic_GENCLS_motor_network_reduction'


def supported(model):
    if any(m.model != 'GENCLS' or m.governor is not None or m.exciter is not None or m.stabilizer is not None for m in model.gen_passports):
        return False
    return not any((model.settings.overexcitation_limiters, model.settings.generator_field_thermal,
                    model.settings.underexcitation_limiters, model.settings.oel2c_limiters,
                    model.settings.esst1a_exciters, model.settings.takeover_oels))


def callback(model):
    if not supported(model):
        return None
    def evaluate(time, state):
        return jacobian(model, state)
    evaluate.jacobian_kind = KIND
    return evaluate


def jacobian(model, state):
    if not supported(model):
        raise ValueError('analytic coupled Jacobian supports fixed-field GENCLS only')
    state = np.asarray(state)
    dimension = model.generator_thermal.end
    if state.shape != (dimension,) or not np.isfinite(state).all():
        raise ValueError('invalid coupled Jacobian state')
    nm = len(model.settings.motors); ng = len(model.generators)
    voltage, im, _, _ = model.electrical(state)
    eg = model.generator_internal(state); ig = model.generator_currents(state, voltage)
    # Reuse the exact single/double-cage and motor heat block. The adapter
    # only restricts its output dimension; its network sees all generators.
    view = SimpleNamespace(**vars(model))
    view.electrical = lambda ignored: (voltage, im, None, None)
    result = np.zeros((dimension, dimension))
    result[:model.motor_state_end, :model.motor_state_end] = motor_jacobian(view, state[:model.motor_state_end])
    model._motor_current_response = view._motor_current_response
    cached = getattr(model, '_classical_voltage_response', None)
    if cached is None or cached[0] is not model.lu:
        rhs = np.zeros((len(model.bus_names), nm + ng), dtype=complex)
        for j, (bus, y) in enumerate(zip(model.motor_buses, model.motor_y)): rhs[bus, j] = y
        for j, (bus, y) in enumerate(zip(model.gen_buses, model.active_gen_y)): rhs[bus, nm+j] = y
        cached = (model.lu, model.lu.solve(rhs)); model._classical_voltage_response = cached
    response = cached[1]
    real = np.arange(nm)*3; imag = real+1; speed = real+2
    delta = model.gen_offset+np.arange(ng)*2; omega = delta+1
    dvm_delta = response[model.motor_buses, nm:] * (1j*eg)[None, :]
    dim_delta = np.where(model.connected[:, None], dvm_delta / model.zmotor[:, None], 0j)
    motor_emf = state[real]+1j*state[imag]
    for i, motor in enumerate(model.settings.motors):
        coefficient = 1j*model.constants[i][1]/model.constants[i][2]
        if i in model.cages.addresses: coefficient = model.cages.coefficients[i][2][0]
        derivative = coefficient*dim_delta[i]
        result[real[i], delta] = derivative.real; result[imag[i], delta] = derivative.imag
        result[speed[i], delta] = (motor_emf[i]*np.conj(dim_delta[i])).real/(2*motor.inertia_s)
        if i in model.thermal_indices:
            result[model.thermal_indices[i], delta] = 2*(np.conj(im[i])*dim_delta[i]).real/motor.thermal.time_constant_s
        if i in model.cages.addresses:
            address = model.cages.addresses[i]; coefficient = model.cages.coefficients[i][2][1]
            derivative = coefficient*dim_delta[i]
            result[address.start, delta] = derivative.real; result[address.start+1, delta] = derivative.imag
    dig_em = np.where(model.gen_connected[:, None], -response[model.gen_buses, :nm]/model.gen_z[:, None], 0j)
    dig_delta = np.where(model.gen_connected[:, None],
                        ((np.eye(ng)-response[model.gen_buses, nm:])*(1j*eg)[None, :])/model.gen_z[:, None], 0j)
    for i, machine in enumerate(model.gen_passports):
        inertia = 2*machine.inertia_s
        power_em = eg[i]*np.conj(dig_em[i])
        result[omega[i], real] = -power_em.real/inertia
        result[omega[i], imag] = -power_em.imag/inertia
        power_delta = eg[i]*np.conj(dig_delta[i]); power_delta[i] += 1j*eg[i]*np.conj(ig[i])
        result[omega[i], delta] = -power_delta.real/inertia
        result[delta[i], omega[i]] = 2*math.pi*model.network.frequency_hz
        result[omega[i], omega[i]] = -machine.damping_pu/inertia
    for i, (address, passport) in model.generator_thermal.entries.items():
        sensitivity = np.conj(ig[i])*dig_em[i]
        result[address, real] = 2*sensitivity.real/passport.time_constant_s
        result[address, imag] = -2*sensitivity.imag/passport.time_constant_s
        result[address, delta] = 2*(np.conj(ig[i])*dig_delta[i]).real/passport.time_constant_s
        result[address, address] = -1/passport.time_constant_s
    if not np.isfinite(result).all():
        raise ValueError('nonfinite coupled Jacobian')
    return result
