"""Карта селективности луча, АВР и разгон скольжения."""

import math

import pytest

from power_grid_env.coordination import qualify, qualify_transfer
from power_grid_env.dynamics import coupled_induction, induction_equilibrium_slip, simulate_induction_slip


def test_coordination_map_follows_fault_location_source_load_and_settings():
    report = qualify()
    assert report["verdict"] == "pass", report
    assert report["current_error"] < 1e-5
    assert report["near_before_far"] is True
    assert report["weak_source_silent"] is True
    assert report["far_blind_at_midpoint"] is True
    assert report["swapped_settings_reverse_order"] is True
    assert report["plant_wide_map"] is False


def test_ats_transfers_a_dead_primary_and_refuses_a_live_angle():
    report = qualify_transfer()
    assert report["verdict"] == "pass", report
    assert report["voltage_error"] < 1e-5
    assert report["transfer_reason"] == "dead_bus"
    assert report["parallel_reason"] == "angle_difference"


def test_induction_runup_matches_the_algebraic_slip_and_halves_with_the_step():
    voltage = 400.0
    rotor_r = 0.2
    reactance = 1.5
    load_power = 2000.0
    inertia = 0.2
    omega = 2.0 * math.pi * 50.0
    analytic = induction_equilibrium_slip(voltage, rotor_r, reactance, load_power)
    settled = simulate_induction_slip(
        terminal_v=voltage,
        rotor_r=rotor_r,
        reactance=reactance,
        load_power_w=load_power,
        inertia_kgm2=inertia,
        omega_sync=omega,
        dt_s=0.002,
        t_end_s=4.0,
    )
    assert abs(float(settled["slip"][-1]) - analytic) < 1e-6

    def at_step(dt_s: float) -> float:
        trace = simulate_induction_slip(
            terminal_v=voltage,
            rotor_r=rotor_r,
            reactance=reactance,
            load_power_w=load_power,
            inertia_kgm2=inertia,
            omega_sync=omega,
            dt_s=dt_s,
            t_end_s=0.4,
        )
        return float(trace["slip"][-1])

    fine = at_step(0.0005)
    coarse = abs(at_step(0.004) - fine)
    halfway = abs(at_step(0.002) - fine)
    assert coarse / halfway == pytest.approx(4.0, rel=0.25)

    with pytest.raises(ValueError):
        induction_equilibrium_slip(voltage, rotor_r, reactance, 80_000.0)
    stalled = simulate_induction_slip(
        terminal_v=voltage,
        rotor_r=rotor_r,
        reactance=reactance,
        load_power_w=80_000.0,
        inertia_kgm2=inertia,
        omega_sync=omega,
        dt_s=0.01,
        t_end_s=0.5,
    )
    assert float(stalled["slip"][-1]) > 1.0

    emf = 400.0 + 0j
    thevenin = 0.02 + 0.05j
    coupled = coupled_induction(emf, thevenin, rotor_r, reactance, load_power)
    assert coupled["stalled"] is False
    networked = simulate_induction_slip(
        terminal_v=voltage,
        rotor_r=rotor_r,
        reactance=reactance,
        load_power_w=load_power,
        inertia_kgm2=inertia,
        omega_sync=omega,
        dt_s=0.002,
        t_end_s=4.0,
        emf=emf,
        thevenin=thevenin,
    )
    assert abs(float(networked["slip"][-1]) - float(coupled["slip"])) < 1e-6
