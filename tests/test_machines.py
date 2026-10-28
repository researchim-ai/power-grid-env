"""Двигатель в сети, порядок пуска, тишина двух машин и остров без grid-forming."""

import math

import pytest

from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder
from power_grid_env.dynamics import (
    airgap_torque,
    coupled_induction,
    induction_equilibrium_slip,
    induction_torque,
    release_status,
    restart_sequence,
    simulate_two_machine,
)


def test_coupled_motor_matches_the_stiff_root_and_stalls_above_breakdown():
    rotor_r = 0.02
    reactance = 0.2
    load = 0.4
    emf = 1.0 + 0.0j
    stiff = coupled_induction(emf, 0.0j, rotor_r, reactance, load)
    assert stiff["stalled"] is False
    assert stiff["slip"] == pytest.approx(induction_equilibrium_slip(1.0, rotor_r, reactance, load), rel=1e-8)
    power = induction_torque(stiff["slip"], abs(stiff["voltage"]), rotor_r, reactance)
    assert power == pytest.approx(load, rel=1e-8)
    assert abs(stiff["current"]) ** 2 * (rotor_r / stiff["slip"]) == pytest.approx(power, rel=1e-8)
    omega = 2.0 * math.pi * 50.0
    assert airgap_torque(power, omega) * omega == pytest.approx(power)
    soft = coupled_induction(emf, 0.05j, rotor_r, reactance, load)
    assert soft["stalled"] is False
    assert soft["slip"] > stiff["slip"]
    assert abs(soft["voltage"]) < abs(emf)
    alone = coupled_induction(emf, 0.02j, rotor_r, reactance, load)
    shared = coupled_induction(emf, 0.02j, rotor_r, reactance, load, parallel=5.0 + 0.0j)
    assert shared["stalled"] is False
    assert abs(shared["voltage"]) < abs(alone["voltage"])
    branch = shared["current"] + shared["voltage"] / (5.0 + 0.0j)
    assert abs(emf - shared["voltage"] - 0.02j * branch) < 1e-9
    assert coupled_induction(emf, 0.0j, rotor_r, reactance, 100.0)["stalled"] is True


def test_restart_waits_for_voltage_and_keeps_priority():
    motors = [{"id": "big", "priority": 2, "drop_pu": 0.2}, {"id": "small", "priority": 1, "drop_pu": 0.08}]
    assert restart_sequence(motors, 0.7, 0.9) == []
    assert restart_sequence(motors, 1.0, 0.9) == ["small", "big"]
    assert restart_sequence(motors, 0.95, 0.9) == ["small"]


def test_two_machines_stay_at_equilibrium_without_a_disturbance():
    pm = 0.4
    emf = 1.1
    reactance = 0.3
    quiet = simulate_two_machine(pm=pm, emf=emf, reactance=reactance, inertia_s=5.0, disturbance_rad=0.0, t_end_s=0.2)
    angle = math.asin(pm * reactance / (emf * emf))
    assert quiet["delta"][-1] == pytest.approx(angle / 2.0, abs=1e-8)
    assert max(abs(float(value)) for value in quiet["equilibrium_residual"]) < 1e-9
    assert release_status()["industrial_rms_qualified"] is False
    assert release_status()["andes_integrated"] is False


def test_an_inverter_island_does_not_receive_an_artificial_slack():
    builder = NetBuilder("island", "Остров", 10.0)
    builder.node("bus")
    builder.inverter("bus", s_rated_va=100_000.0, p_w=20_000.0, inverter_id="pv")
    builder.load("bus", 20_000.0, 0.0, load_id="load")
    solved = solve_network(builder.build())
    assert solved.status == "deenergized_no_source"
    assert solved.diagnostics.get("physical_condition") == "no_grid_forming_source"
    assert all(abs(item.complex) == 0.0 for item in solved.voltages)
