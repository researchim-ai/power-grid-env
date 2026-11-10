"""Resistively commutated three-wire six-pulse diode bridge.

No smoothing of the blocking law, current clipping, artificial capacitance or
reverse power. Source/commutation inductance inside the bridge is excluded;
upstream RLC dynamics belong to the phase network.
"""
import math
import numpy as np


def bridge_port(voltage_ab, dc_voltage_v, parameters, *, connected, bypassed):
    """Return injection-positive alpha-beta current and passive port accounting.

Finite per-diode resistance resolves tied phases and permits continuous
resistive overlap without an arbitrary phase-index choice.
"""
    voltage = complex(voltage_ab)
    if not all(math.isfinite(v) for v in (voltage.real, voltage.imag, dc_voltage_v)) or dc_voltage_v < 0:
        raise ValueError('finite terminal voltage and nonnegative DC voltage required')
    phases = np.array([voltage.real, -.5*voltage.real+math.sqrt(3)/2*voltage.imag,
                       -.5*voltage.real-math.sqrt(3)/2*voltage.imag])
    resistance = parameters.series_resistance_ohm + (0. if bypassed else parameters.precharge_resistance_ohm)
    leg_r = parameters.diode_resistance_ohm
    current = 0.;phase_current = np.zeros(3)
    if connected and max(phases)-min(phases)>dc_voltage_v+2*parameters.diode_drop_v:
        order=np.argsort(phases)
        # Positive per-diode resistance makes the terminal-current map continuous
        # through commutation, including simultaneous three-diode conduction.
        for top_count,bottom_count in ((1,1),(2,1),(1,2)):
            top=order[-top_count:];bottom=order[:bottom_count]
            current=(np.mean(phases[top])-np.mean(phases[bottom])-2*parameters.diode_drop_v-dc_voltage_v)/(resistance+leg_r/top_count+leg_r/bottom_count)
            upper=np.mean(phases[top])-parameters.diode_drop_v-leg_r*current/top_count
            lower=np.mean(phases[bottom])+parameters.diode_drop_v+leg_r*current/bottom_count
            up=np.maximum(0.,(phases-upper-parameters.diode_drop_v)/leg_r)
            down=np.maximum(0.,(lower-phases-parameters.diode_drop_v)/leg_r)
            if current>=0 and abs(sum(up)-current)<=1e-8*(1+current) and abs(sum(down)-current)<=1e-8*(1+current):
                phase_current=down-up;break
        else:raise ValueError('no consistent diode conduction state')
    injection = complex(2/3*(phase_current[0]-.5*phase_current[1]-.5*phase_current[2]),
                        (phase_current[1]-phase_current[2])/math.sqrt(3))
    return injection, dict(rectifier_current_a=current, rectifier_dc_power_w=dc_voltage_v*current,
        grid_power_w=float(-phases@phase_current), rectifier_loss_w=resistance*current**2+leg_r*float(phase_current@phase_current)+2*parameters.diode_drop_v*current,
        precharge_loss_w=0. if bypassed else parameters.precharge_resistance_ohm*current**2,
        precharge_bypassed=bool(bypassed), rectifier_conducting=current > 0.)
