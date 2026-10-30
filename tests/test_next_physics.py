"""Звено RLC, собственная частота ANDES и пул процессов."""

import math

import pytest

from power_grid_env.andes_rms import qualify_swing_mode
from power_grid_env.backend_opendss import solve_network
from power_grid_env.build import NetBuilder
from power_grid_env.emt_network import phasor_node, qualify_rlc_link
from power_grid_env.topology import compile_topology
from power_grid_env.worker_pool import SolverPool


def test_rlc_link_matches_the_phasor_and_conserves_energy():
    report = qualify_rlc_link()
    assert report["verdict"] == "pass", report
    assert report["voltage_error"] < 1e-4
    assert 3.0 < report["step_ratio"] < 5.0
    assert abs(report["energy_imbalance_j"]) < 1e-6
    assert report["full_network_emt"] is False
    with pytest.raises(ValueError):
        phasor_node(1000.0, 1.0, 0.02, 0.0, 40.0, 50.0)


def test_andes_swing_eigenvalue_matches_the_analytic_frequency():
    report = qualify_swing_mode()
    assert report["verdict"] == "pass", report
    assert report["frequency_error"] < 1e-6
    assert abs(report["real_part"]) < 1e-6
    assert report["industrial_rms_qualified"] is False


def test_solver_pool_matches_the_direct_solve_and_replaces_a_dead_worker():
    light = _network(2_000_000.0)
    heavy = _network(6_000_000.0)
    direct_light = _voltage(light, solve_network(light))
    direct_heavy = _voltage(heavy, solve_network(heavy))
    assert abs(direct_light - direct_heavy) / direct_light > 0.01
    pool = SolverPool(2)
    try:
        pooled_light = _voltage(light, pool.solve(0, light))
        pooled_heavy = _voltage(heavy, pool.solve(1, heavy))
        assert pooled_light == pytest.approx(direct_light, rel=1e-5)
        assert pooled_heavy == pytest.approx(direct_heavy, rel=1e-5)
        pool.workers[0]._kill()
        with pytest.raises(RuntimeError):
            pool.solve(0, light)
        pool.replace(0)
        restored = _voltage(light, pool.solve(0, light))
        assert restored == pytest.approx(direct_light, rel=1e-5)
        untouched = _voltage(heavy, pool.solve(1, heavy))
        assert untouched == pytest.approx(direct_heavy, rel=1e-5)
    finally:
        pool.close()


def _network(active_w: float):
    builder = NetBuilder("pool", "Пул", 10.0, s_base_va=1.0e6)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.05, 0.1)
    builder.line_z1("src", "ld", 0.3 + 0.8j, line_id="line")
    builder.load("ld", active_w, active_w * 0.2, load_id="load")
    return builder.build()


def _voltage(network, solved) -> float:
    assert solved.status == "converged"
    bus = compile_topology(network).node_to_bus["ld"]
    voltage = next(item.complex for item in solved.voltages if item.bus_id == bus and item.phase == "A")
    return abs(voltage) / (10_000.0 / math.sqrt(3.0))
