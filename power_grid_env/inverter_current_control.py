"""Synchronous-frame PI current loop in stationary alpha-beta coordinates.

The reference frame is prescribed; this is not rotor-flux orientation or a
speed/torque controller. Core-conductance feedthrough is solved algebraically
before circular voltage saturation. Anti-windup uses tracking back-calculation.
"""
import numpy as np


def current_control(parameters, t, angle, omega, integrator, offset, conductance, voltage_limit):
    target=complex(parameters.direct_peak_a,parameters.quadrature_peak_a)*min(max(t/parameters.ramp_s,0.),1.)*np.exp(1j*angle)
    numerator=parameters.kp_ohm*(target-offset)+integrator
    unlimited=numerator/(1+parameters.kp_ohm*conductance)
    applied=unlimited if abs(unlimited)<=voltage_limit else unlimited*(voltage_limit/abs(unlimited))
    current=offset+conductance*applied
    error=target-current
    # PI output before saturation, evaluated at the actual current (not at the
    # unconstrained current); this is required for nonzero core feedthrough.
    demanded=parameters.kp_ohm*error+integrator
    derivative=1j*omega*integrator+parameters.ki_ohm_per_s*error+parameters.antiwindup_per_s*(applied-demanded)
    return applied,derivative,dict(current_reference_alpha_a=float(target.real),current_reference_beta_a=float(target.imag),
        current_tracking_error_a=float(abs(error)),inverter_voltage_alpha_v=float(applied.real),inverter_voltage_beta_v=float(applied.imag),
        current_control_limited=abs(unlimited)>voltage_limit)
