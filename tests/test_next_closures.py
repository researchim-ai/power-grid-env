"""Следующий пакет расчётов: стабилизатор, отключения, провал, пик и оценка."""

import math

import numpy as np
import pytest
from scipy.linalg import expm

from power_grid_env.analysis import drop_measurement
from power_grid_env.der import apply_ramp, qualify_round_trip
from power_grid_env.dynamics import (
    equilibrium,
    induction_equilibrium_slip,
    natural_frequency,
    pss_derivative,
    pss_state_matrix,
    sag_then_restore,
    simulate_pss,
    state_from_terminal_voltage,
)
from power_grid_env.fault_envelope import waveform_kappa
from power_grid_env.iec60909 import peak_factor
from power_grid_env.limit_mutations import _case
from power_grid_env.network_opf import audit_feasibility, solve_ac_opf
from power_grid_env.outage import critical_unserved_j, qualify_ramp_power, qualify_transformers, qualify_unserved
from dataclasses import replace


def test_washout_pss_matches_the_linear_matrix_and_rests_at_zero():
    pm, field, voltage, reactance = 0.5, 1.1, 1.0, 0.3
    inertia, gain, washout = 4.0, 0.2, 0.5
    omega = 2.0 * math.pi * 50.0
    angle, _speed = equilibrium(pm, field, voltage, reactance, omega)
    horizon = 0.05
    matrix = pss_state_matrix(angle, field, voltage, reactance, inertia, omega, gain, washout)

    def at_step(dt_s: float) -> np.ndarray:
        trace = simulate_pss(
            pm=pm,
            e_pu=field,
            v_pu=voltage,
            x_pu=reactance,
            inertia_s=inertia,
            gain=gain,
            washout_s=washout,
            dt_s=dt_s,
            t_end_s=horizon,
            disturbance_rad=1.0e-4,
        )
        return np.array([trace["delta"][-1] - angle, trace["omega"][-1] - omega, trace["filter"][-1]])

    fine = at_step(0.00025)
    coarse = np.linalg.norm(at_step(0.002) - fine)
    halfway = np.linalg.norm(at_step(0.001) - fine)
    assert coarse / halfway == pytest.approx(4.0, rel=0.25)
    linear = expm(matrix * horizon) @ np.array([1.0e-4, 0.0, 0.0])
    assert np.linalg.norm(fine - linear) / np.linalg.norm(linear) < 1e-4
    quiet = simulate_pss(
        pm=pm, e_pu=field, v_pu=voltage, x_pu=reactance, inertia_s=inertia, gain=gain, washout_s=washout, dt_s=0.01, t_end_s=1.0
    )
    assert max(abs(float(quiet["delta"][-1]) - angle), abs(float(quiet["filter"][-1]))) < 1e-8
    blocked = pss_derivative(np.array([angle, omega + 1.0, 1.0]), pm, field, voltage, reactance, inertia, omega, gain, washout)
    assert blocked[2] == pytest.approx(0.0)
    assert gain * ((omega + 1.0 - omega) - 1.0) == 0.0
    bare = pss_state_matrix(angle, field, voltage, reactance, inertia, omega, 0.0, washout)
    swing = max(abs(value.imag) for value in np.linalg.eigvals(bare))
    assert swing == pytest.approx(natural_frequency(angle, field, voltage, reactance, inertia, omega))


def test_parallel_transformers_do_not_double_and_match_the_divider():
    report = qualify_transformers()
    assert report["verdict"] == "pass", report
    assert report["current_error"] < 1e-4
    assert abs(report["post_to_pre"] - 2.0) / 2.0 > 0.02


def test_critical_energy_is_unserved_only_while_its_bus_is_dark():
    report = qualify_unserved()
    assert report["verdict"] == "pass", report
    assert report["dark_j"] == pytest.approx(100_000.0 * 3600.0)
    assert report["spur_open_j"] == 0.0
    with pytest.raises(ValueError):
        critical_unserved_j([("a", 1.0, True)], {"a": 0.0}, -1.0)


def test_short_sag_returns_and_a_long_sag_runs_away():
    voltage, rotor_r, reactance, load = 400.0, 0.2, 1.5, 20_000.0
    omega = 2.0 * math.pi * 50.0
    stable = induction_equilibrium_slip(voltage, rotor_r, reactance, load)
    coefficient = load * reactance * reactance
    linear = -voltage * voltage * rotor_r
    constant = load * rotor_r * rotor_r
    unstable = (-linear + math.sqrt(linear * linear - 4.0 * coefficient * constant)) / (2.0 * coefficient)
    assert stable < unstable < 1.0
    short = sag_then_restore(
        slip0=stable, v_nominal=voltage, v_sag=80.0, rotor_r=rotor_r, reactance=reactance,
        load_power_w=load, inertia_kgm2=0.2, omega_sync=omega, sag_s=0.3, recover_s=4.0, dt_s=0.002,
    )
    long = sag_then_restore(
        slip0=stable, v_nominal=voltage, v_sag=80.0, rotor_r=rotor_r, reactance=reactance,
        load_power_w=load, inertia_kgm2=0.2, omega_sync=omega, sag_s=1.2, recover_s=0.4, dt_s=0.002,
    )
    assert short["slip_after_sag"] < unstable
    assert short["slip_after_recovery"] == pytest.approx(stable, abs=1e-4)
    assert long["slip_after_sag"] > unstable
    assert long["slip_after_recovery"] > long["slip_after_sag"] + 0.05


def test_storage_round_trip_returns_the_soc_and_clips_the_discharge():
    report = qualify_round_trip()
    assert report["verdict"] == "pass", report
    assert report["charge_w"] > 1000.0


def test_ramp_limits_the_step_and_the_phase_network_takes_that_power():
    report = qualify_ramp_power()
    assert report["verdict"] == "pass", report
    assert report["ramped_w"] == 25_000.0
    assert report["power_error"] < 1e-4
    assert apply_ramp(0.0, 10_000.0, 25_000.0, 1.0) == 10_000.0


def test_dropping_a_measurement_keeps_or_loses_observability():
    values = np.array([1.0, 1.0, 2.0])
    matrix = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    weights = np.ones(3)
    kept = drop_measurement(values, matrix, weights, 0)
    assert kept["observable"] is True
    assert kept["estimate"] == pytest.approx([1.0, 2.0])
    lost = drop_measurement(values, matrix, weights, 2)
    assert lost["observable"] is False
    assert lost["estimate"] is None


def test_waveform_peak_stays_with_the_iec_kappa():
    for ratio in (0.1, 0.3, 0.8):
        measured = waveform_kappa(ratio, 1.0)
        assert measured == pytest.approx(peak_factor(ratio), rel=0.01)


def test_power_flow_voltage_starts_the_machine_without_a_step():
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.build import NetBuilder
    from power_grid_env.topology import compile_topology

    emf = 10_000.0 / math.sqrt(3.0)
    z_source = 0.05 + 0.1j
    z_line = 0.3 + 0.8j
    z_load = 80.0 + 8.0j
    expected = emf * z_load / (z_source + z_line + z_load)
    builder = NetBuilder("handoff", "Старт", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", z_source.real, z_source.imag)
    builder.line_z1("src", "ld", z_line, line_id="line")
    power = 3.0 * (emf**2 / np.conj(z_load))
    builder.load("ld", float(power.real), float(power.imag), model="constant_z", load_id="load")
    network = builder.build()
    solved = solve_network(network)
    assert solved.status == "converged"
    bus = compile_topology(network).node_to_bus["ld"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    assert abs(voltage - expected) / emf < 1e-5
    measured = -sum(item.p_into_w for item in solved.flows if item.equipment_id == "source" and item.phase in "ABC")
    state = state_from_terminal_voltage(measured / 1.0e6, 1.2, abs(voltage) / emf, 0.4, 5.0, 1.0, 2.0 * math.pi * 50.0)
    assert state["steps"] == 0.0
    assert state["residual_max"] < 1e-9
    with pytest.raises(ValueError):
        equilibrium(5.0, 1.2, abs(voltage) / emf, 0.4, 2.0 * math.pi * 50.0)


def test_a_tight_angle_limit_rejects_the_saved_dispatch():
    case = _case()
    solved = solve_ac_opf(case)
    vm = np.array(solved["vm_pu"])
    theta = np.radians(solved["va_deg"])
    pg = np.array(solved["pg_mw"])
    qg = np.array(solved["qg_mvar"])
    assert audit_feasibility(case, vm, theta, pg, qg)["angles_within_limit"] is True
    tight = replace(case.branches[0], angmax_deg=0.01, angmin_deg=-0.01)
    rejected = audit_feasibility(replace(case, branches=[tight]), vm, theta, pg, qg)
    assert rejected["angles_within_limit"] is False
    assert rejected["feasible"] is False
