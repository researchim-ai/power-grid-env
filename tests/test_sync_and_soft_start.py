"""Синхронизм при включении и пуск заторможенного двигателя половинным напряжением."""

import math

import pytest

from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder
from power_grid_env.dynamics import induction_torque
from power_grid_env.motor_network import three_phase_power
from power_grid_env.protection import sync_allows
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine
from power_grid_env.topology import compile_topology


def test_sync_check_accepts_a_dead_side_and_rejects_a_live_angle():
    assert sync_allows(1000 + 0j, 0j, 1000, 1000, max_angle_rad=0.2, max_voltage_pu=0.1, dead_pu=0.05)[0] is True
    live = sync_allows(
        1000 + 0j,
        1000 * (math.cos(0.5) + 1j * math.sin(0.5)),
        1000,
        1000,
        max_angle_rad=0.2,
        max_voltage_pu=0.1,
        dead_pu=0.05,
    )
    assert live == (False, "angle_difference")

    matched = NetBuilder("sync-ok", "Синхронизм", 10.0)
    matched.node("src")
    matched.node("dead")
    matched.source("src", 0.05, 0.2)
    matched.switch("src", "dead", switch_id="sw", closed=False, sync_check=True)
    matched.load("dead", 50_000.0, 0.0, model="constant_z", load_id="load")
    allowed = Engine(matched.build(), Scenario(id="sync-ok", horizon_s=10, settle_s=100, control_interval_s=10))
    allowed.reset()
    decision = allowed.submit({"kind": "switch", "equipment_id": "sw", "command": "close"})
    assert decision["accepted"] is True
    assert decision["reason"] == "dead_bus"
    allowed.advance_to(0.01)
    assert allowed.net.switches[0].closed is True

    shifted = NetBuilder("sync-bad", "Угол", 10.0)
    shifted.node("left")
    shifted.node("right")
    shifted.source("left", 0.05, 0.2, source_id="grid")
    shifted.source("right", 0.05, 0.2, source_id="plant", angle_rad=0.5, is_slack=False)
    shifted.switch("left", "right", switch_id="tie", closed=False, sync_check=True, sync_max_angle_rad=0.2)
    blocked = Engine(shifted.build(), Scenario(id="sync-bad", horizon_s=10, settle_s=100, control_interval_s=10))
    blocked.reset()
    refused = blocked.submit({"kind": "switch", "equipment_id": "tie", "command": "close"})
    assert refused["accepted"] is False
    assert refused["reason"] == "angle_difference"
    assert blocked.net.switches[0].closed is False


def test_half_voltage_halves_locked_rotor_current_and_quarters_torque():
    rotor_r = 15.0
    reactance = 80.0
    full = 10_000.0 / math.sqrt(3.0)
    assert induction_torque(1.0, full / 2.0, rotor_r, reactance) == pytest.approx(
        0.25 * induction_torque(1.0, full, rotor_r, reactance)
    )
    active, reactive = three_phase_power(rotor_r + 1j * reactance, full)

    def current(line_voltage: float) -> float:
        builder = NetBuilder("soft", "Пуск", 10.0, s_base_va=1.0e6)
        builder.node("src")
        builder.node("ld")
        builder.source("src", 0.05, 0.2)
        builder.line_z1("src", "ld", 0.2 + 0.4j, line_id="line")
        builder.load("ld", active, reactive, model="constant_z", load_id="motor")
        network = builder.build()
        network.sources[0].ll_v = line_voltage
        network.sources[0].r0_ohm = 0.05
        network.sources[0].x0_ohm = 0.2
        solved = solve_network(network)
        assert solved.status == "converged"
        bus = compile_topology(network).node_to_bus["src"]
        return abs(next(item.current for item in solved.flows if item.equipment_id == "source" and item.bus_id == bus and item.phase == "A"))

    assert current(10_000.0) / current(5_000.0) == pytest.approx(2.0, rel=1e-5)
