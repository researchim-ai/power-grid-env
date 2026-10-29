"""Grid-forming как Тевенин и групповой пуск двигателей на одном фидере."""

import math

import numpy as np
import pytest

from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder
from power_grid_env.checks import kcl_report
from power_grid_env.dynamics import coupled_induction, feeder_restart
from power_grid_env.topology import compile_topology


def _forming_reference(vln: float, impedance: complex, power: complex) -> complex:
    z_load = vln**2 / np.conj(power)
    return vln * z_load / (impedance + z_load)


def test_forming_inverter_matches_the_thevenin_and_does_not_invent_a_current_limit():
    builder = NetBuilder("form", "Опора", 10.0, s_base_va=1.0e6)
    builder.node("bus")
    builder.inverter(
        "bus",
        s_rated_va=1.0e6,
        p_w=0.0,
        forming=True,
        forming_r_ohm=0.05,
        forming_x_ohm=0.2,
        inverter_id="grid",
    )
    builder.load("bus", 100_000.0, 20_000.0, model="constant_z", load_id="load")
    network = builder.build()
    solved = solve_network(network)
    assert solved.status == "converged", solved.warnings
    vln = 10_000.0 / math.sqrt(3.0)
    expected = _forming_reference(vln, 0.05 + 0.2j, (100_000.0 + 20_000.0j) / 3.0)
    bus = compile_topology(network).node_to_bus["bus"]
    actual = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    assert abs(abs(actual) - abs(expected)) / vln < 1e-5
    assert abs(np.angle(actual / expected)) < 1e-4
    assert kcl_report(network, solved)["mismatch_pu"] < 1e-8
    assert solved.diagnostics["forming_over_rating"] == []

    overloaded = network.model_copy(deep=True)
    overloaded.inverters[0].s_rated_va = 10_000.0
    over = solve_network(overloaded)
    assert over.status == "converged"
    assert over.diagnostics["forming_over_rating"] == ["grid"]
    over_v = next(item.complex for item in over.voltages if item.bus_id == bus and item.phase == "A")
    assert abs(abs(over_v) - abs(expected)) / vln < 1e-5


def test_forming_power_limit_scales_the_internal_voltage_when_asked():
    builder = NetBuilder("limit", "Предел", 10.0, s_base_va=1.0e6)
    builder.node("bus")
    builder.inverter(
        "bus",
        s_rated_va=20_000.0,
        p_w=0.0,
        forming=True,
        forming_limit=True,
        forming_r_ohm=0.05,
        forming_x_ohm=0.2,
        inverter_id="grid",
    )
    builder.load("bus", 100_000.0, 20_000.0, model="constant_z", load_id="load")
    solved = solve_network(builder.build())
    assert solved.status == "converged", solved.warnings
    assert solved.diagnostics["forming_limited"] == ["grid"]
    assert solved.diagnostics["forming_over_rating"] == []
    delivered = math.hypot(
        sum(flow.p_into_w for flow in solved.flows if flow.equipment_id == "grid" and flow.phase in "ABC"),
        sum(flow.q_into_var for flow in solved.flows if flow.equipment_id == "grid" and flow.phase in "ABC"),
    )
    assert delivered == pytest.approx(20_000.0, rel=1e-4)


def test_following_inverter_still_does_not_energize_an_island():
    builder = NetBuilder("follow", "Следование", 10.0)
    builder.node("bus")
    builder.inverter("bus", s_rated_va=100_000.0, p_w=10_000.0, inverter_id="pv")
    builder.load("bus", 10_000.0, 0.0, load_id="load")
    solved = solve_network(builder.build())
    assert solved.status == "deenergized_no_source"
    assert solved.diagnostics["physical_condition"] == "no_grid_forming_source"


def test_feeder_restart_stops_when_the_next_motor_would_collapse_the_voltage():
    emf = 1.0 + 0.0j
    z_line = 0.03j
    light = {"id": "m1", "priority": 1, "rotor_r": 0.02, "reactance": 0.2, "load_torque": 0.3}
    second = {"id": "m2", "priority": 2, "rotor_r": 0.02, "reactance": 0.2, "load_torque": 0.3}
    heavy = {"id": "m3", "priority": 3, "rotor_r": 0.02, "reactance": 0.2, "load_torque": 50.0}
    alone = coupled_induction(emf, z_line, light["rotor_r"], light["reactance"], light["load_torque"])
    single = feeder_restart(emf, z_line, [light], minimum_v=0.5)
    assert single["started"] == ["m1"]
    assert single["slips"]["m1"] == pytest.approx(alone["slip"], rel=1e-6)
    assert abs(single["voltage"] - alone["voltage"]) < 1e-6
    both = feeder_restart(emf, z_line, [second, light], minimum_v=0.5)
    assert both["started"] == ["m1", "m2"]
    assert abs(both["voltage"]) < abs(single["voltage"])
    stopped = feeder_restart(emf, z_line, [heavy, second, light], minimum_v=0.5)
    assert stopped["started"] == ["m1", "m2"]
    assert "m3" not in stopped["slips"]


def test_forming_without_impedance_is_rejected():
    builder = NetBuilder("bare", "Без Z", 10.0)
    builder.node("bus")
    with pytest.raises(ValueError, match="forming_r_ohm"):
        builder.inverter("bus", s_rated_va=10_000.0, p_w=0.0, forming=True, inverter_id="grid")
        builder.build()
