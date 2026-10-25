import math
from pathlib import Path

import numpy as np

from power_grid_env.analysis import ac_opf_two_bus, dc_opf, dc_power_flow, line_flow_pu, lodf, ptdf_column, wls_dc
from power_grid_env.build import NetBuilder
from power_grid_env.dynamics import equilibrium, natural_frequency, simulate_smib
from power_grid_env.emt import averaged_buck_current, cosim_rl_energy, rl_exact, rl_trapezoidal, rlc_exact_underdamped, rlc_trapezoidal, switching_buck_average
from power_grid_env.llm_tools import ToolSession
from power_grid_env.numerical_suite import run_numerical_suite
from power_grid_env.protection import advance_relay, iec_operate_time
from power_grid_env.protection import RelayState
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine, agent_model
from power_grid_env.vlm import hit_test, observation_svg, pixels_only_html


def test_numerical_suite():
    results = run_numerical_suite()
    failed = [item for item in results if not item["passed"]]
    assert not failed, failed


def test_iec_curve_literal():
    # t = 0.14 / (2**0.02 - 1) = 10.029 с при TMS = 1, I/Is = 2.
    assert abs(iec_operate_time(2.0, 1.0, "standard_inverse", 1.0) - 10.028978) < 1e-4
    state = RelayState("r", "overcurrent", "line", pickup=1.0, curve="definite", time_multiplier_s=1.0, breaker_delay_s=0.0)
    assert advance_relay(state, 2.0, 0.4) is None
    assert advance_relay(state, 2.0, 0.6) == "trip"


def test_smib_period_and_equilibrium():
    pm, e_pu, v_pu, x_pu, inertia = 0.5, 1.1, 1.0, 0.3, 4.0
    delta0, omega0 = equilibrium(pm, e_pu, v_pu, x_pu, 2 * math.pi * 50)
    residual = simulate_smib(pm=pm, e_pu=e_pu, v_pu=v_pu, x_pu=x_pu, inertia_s=inertia, delta0=delta0, omega0=omega0, t_end_s=0.01, dt_s=0.01)
    assert np.linalg.norm(residual["equilibrium_residual"]) < 1e-8
    wn = natural_frequency(delta0, e_pu, v_pu, x_pu, inertia, omega0)
    period = 2 * math.pi / wn
    run = simulate_smib(
        pm=pm, e_pu=e_pu, v_pu=v_pu, x_pu=x_pu, inertia_s=inertia,
        delta0=delta0 + 0.03, omega0=omega0, dt_s=0.002, t_end_s=2.0,
    )
    signal = run["delta"] - delta0
    crossings = []
    for index in range(1, len(signal)):
        if signal[index - 1] <= 0 < signal[index] or signal[index - 1] >= 0 > signal[index]:
            crossings.append(run["t"][index])
    measured = crossings[2] - crossings[0]
    assert abs(measured - period) / period < 0.05


def test_emt_matches_closed_form():
    exact = rl_exact(10.0, 1.0, 0.1, 0.2)
    series = rl_trapezoidal(10.0, 1.0, 0.1, 1e-4, 2000)
    assert abs(series[-1] - exact) < 1e-3
    time = np.linspace(0.0, 0.05, 500)
    reference = rlc_exact_underdamped(10.0, 0.2, 0.01, 0.001, time)
    numeric = rlc_trapezoidal(10.0, 0.2, 0.01, 0.001, time[1] - time[0], len(time) - 1)
    assert np.max(np.abs(numeric - reference)) < 0.05
    average = averaged_buck_current(0.4, 20.0, 2.0)
    switched = switching_buck_average(0.4, 20.0, 2.0, 0.002, 1e-5, 40, 5000)
    assert abs(switched - average) / average < 0.05
    energy = cosim_rl_energy(10.0, 1.0, 0.05, 50.0, 1e-4, 0.002, 0.1)
    assert abs(energy["imbalance_j"]) < 0.05 * max(1.0, abs(energy["energy_in_j"]))


def test_dc_wls_and_opf():
    x_pu = 0.2
    susceptance = np.array([[1 / x_pu, -1 / x_pu], [-1 / x_pu, 1 / x_pu]])
    theta = dc_power_flow(susceptance, np.array([0.4, -0.4]), slack=0)
    assert abs(line_flow_pu(theta, 0, 1, x_pu) - 0.4) < 1e-9
    column = ptdf_column(susceptance, 0, [(0, 1, x_pu)], 1)
    assert abs(column[0] + 1) < 1e-8
    try:
        lodf(1.0, 0.2)
        raise AssertionError("LODF не должен существовать при разделении сети")
    except ValueError:
        pass
    estimate = wls_dc(np.array([0.2, 0.2]), np.array([[1.0], [1.0]]), np.array([0.01, 0.01]))
    assert estimate["observable"]
    assert abs(estimate["estimate"][0] - 0.2) < 1e-9
    bad = wls_dc(np.array([0.2, 0.21, 5.0]), np.ones((3, 1)), np.array([0.01, 0.01, 0.01]))
    assert abs(bad["normalized"][2]) > abs(bad["normalized"][0])
    opf = dc_opf(np.array([10.0, 30.0]), np.array([0.0, 0.0]), np.array([1.0, 1.0]), 0.8)
    assert opf["success"]
    assert abs(opf["generation_pu"][0] - 0.8) < 1e-8
    ac = ac_opf_two_bus(0.5, 0.1, 0.25, cost=20.0, pmax=1.0)
    assert ac["success"]
    assert ac["global_optimum_claimed"] is False


def test_snapshot_and_no_leak():
    builder = NetBuilder("snap", "snap", 12.47)
    builder.node("src")
    builder.node("a")
    builder.node("b")
    builder.source("src", 0.02, 0.08)
    builder.switch("src", "a", switch_id="sw", operate_time_s=0.0)
    builder.line_z1("a", "b", complex(0.1, 0.2), line_id="line")
    builder.load("b", 100e3, 10e3, load_id="load")
    builder.meter("b", meter_id="meter")
    network = builder.build()
    scenario = Scenario(id="snap", seed=3, horizon_s=120, control_interval_s=60, settle_s=600, hidden_canary="HIDDEN-CANARY")
    engine = Engine(network, scenario)
    engine.reset()
    engine.submit({"kind": "switch", "equipment_id": "sw", "command": "open"})
    snapshot = engine.snapshot()
    engine.advance_to(60)
    first = engine.last_result.voltages
    other = Engine(network, scenario)
    other.restore(snapshot)
    other.advance_to(60)
    second = other.last_result.voltages
    for left, right in zip(first, second):
        assert abs(left.complex - right.complex) < 1e-6
    observation = engine.observation()
    assert "HIDDEN-CANARY" not in str(observation)
    public = scenario.public_config(agent_model(network, scenario).model_dump(mode="json"))
    assert "HIDDEN-CANARY" not in str(public)
    tools = ToolSession(engine, agent_model(network, scenario))
    assert "HIDDEN-CANARY" not in str(tools.call("get_observation"))
    svg = observation_svg(observation)
    assert "HIDDEN-CANARY" not in svg
    page = pixels_only_html()
    assert "equipment_id" not in page
    assert hit_test(40, 120) == "source"


def test_reference_cases_are_data():
    root = Path("cases/reference")
    assert (root / "README.md").exists()
