import json
import math
from pathlib import Path

import numpy as np
import pytest

from power_grid_env.analysis import ac_opf_two_bus, dc_opf_network
from power_grid_env.backend_opendss import solve_network
from power_grid_env.benchmarks import time_two_bus
from power_grid_env.build import NetBuilder
from power_grid_env.dynamics import simulate_two_machine
from power_grid_env.evaluator import energy_from_service_log
from power_grid_env.importer import import_network
from power_grid_env.industrial import build_industrial_plant
from power_grid_env.runner import _verdict, experiment_fingerprint
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine, agent_model


def _feeder():
    builder = NetBuilder("trust", "trust", 12.47)
    builder.node("src")
    builder.node("end")
    builder.source("src", 0.02, 0.08)
    builder.line_z1("src", "end", 0.1 + 0.2j, line_id="line")
    builder.load("end", 100_000, 10_000, load_id="load")
    builder.meter("end")
    return builder


def _injection(result, equipment_id: str) -> float:
    return -sum(flow.p_into_w for flow in result.flows if flow.equipment_id == equipment_id and flow.phase in "ABC")


def test_what_if_ignores_hidden_fault_impedance():
    network = _feeder().build()
    public = agent_model(network, Scenario(id="hidden", fault_line_id="line", fault_disclosed=False, fault_r_ohm=1.0))
    answers = []
    for resistance in (1.234567, 9.0):
        engine = Engine(network, Scenario(id="hidden", fault_line_id="line", fault_disclosed=False, fault_r_ohm=resistance, horizon_s=120, settle_s=600))
        seen = []

        def capture(model, seen=seen):
            seen.append([fault.r_ohm for fault in model.study_faults])
            return solve_network(model)

        engine.solve = capture
        engine.reset()
        seen.clear()
        engine.what_if([], public)
        answers.append(tuple(seen[0]) if seen and seen[0] else ())
    assert answers[0] == answers[1] == ()


def test_setpoint_is_executed_and_resolved():
    builder = _feeder()
    builder.inverter("end", s_rated_va=80_000, p_w=10_000, inverter_id="pv")
    engine = Engine(builder.build(), Scenario(id="setpoint", horizon_s=120, settle_s=600))
    engine.reset()
    record = engine.step_control({"kind": "setpoint", "equipment_id": "pv", "value": 50_000, "unit": "W"})
    fresh = solve_network(engine.net)
    assert record["acceptance"]["executed_value"] == pytest.approx(50_000)
    assert _injection(engine.last_result, "pv") == pytest.approx(_injection(fresh, "pv"), rel=1e-4)
    assert _injection(engine.last_result, "pv") == pytest.approx(50_000, rel=0.05)


def test_generator_setpoint_changes_power():
    builder = _feeder()
    builder.generator("end", 10_000, generator_id="gen")
    engine = Engine(builder.build(), Scenario(id="generator", horizon_s=120, settle_s=600))
    engine.reset()
    record = engine.step_control({"kind": "setpoint", "equipment_id": "gen", "value": 50_000, "unit": "W"})
    assert record["acceptance"]["accepted"]
    assert engine.net.generators[0].p_w == pytest.approx(50_000)


def test_profile_and_new_measurement_advance_without_switching():
    engine = Engine(
        _feeder().build(),
        Scenario(id="profile", horizon_s=120, settle_s=600, load_profile={"load": [[0, 1], [30, 2]]}),
    )
    engine.reset()
    engine.step_control({"kind": "hold"})
    assert sum(engine.net.loads[0].p_w.values()) == pytest.approx(200_000)
    produced = [sample["produced_s"] for sample in engine.measurements]
    assert 0.0 in produced
    assert any(stamp >= 30 for stamp in produced)


def test_breaker_relay_sees_current_and_trips():
    builder = NetBuilder("relay", "relay", 12.47)
    builder.node("src")
    builder.node("a")
    builder.node("end")
    builder.source("src", 0.02, 0.08)
    builder.switch("src", "a", switch_id="sw")
    builder.line_z1("a", "end", 0.1 + 0.2j, line_id="line")
    builder.load("end", 100_000, 10_000, load_id="load")
    engine = Engine(
        builder.build(),
        Scenario(
            id="relay",
            horizon_s=120,
            settle_s=600,
            protection=[{"id": "r", "equipment_id": "sw", "kind": "overcurrent", "pickup": 0.1, "curve": "definite", "time_multiplier_s": 1}],
        ),
    )
    engine.reset()
    assert engine._relay_signal(engine.relays[0]) > 1.0
    engine.step_control({"kind": "hold"})
    assert engine.net.switches[0].closed is False


def test_unsupported_dss_is_rejected(tmp_path: Path):
    source = Path("cases/acceptance/feeder_north/feeder.dss").read_text(encoding="utf-8")
    modified = tmp_path / "broken.dss"
    modified.write_text(source + "\nNew Transformer.extra phases=3 windings=2\nRedirect hidden.dss\n", encoding="utf-8")
    imported, report = import_network(modified, Path("cases/acceptance/feeder_north/pge-import.yaml"))
    assert imported is None
    assert report["errors"]
    assert any(row["status"] == "unsupported" for row in report["commands"])


def test_json_checkpoint_roundtrip_continues():
    engine = Engine(_feeder().build(), Scenario(id="checkpoint", horizon_s=180, settle_s=600))
    engine.reset()
    engine.step_control({"kind": "hold"})
    raw = json.loads(json.dumps(engine.snapshot()))
    restored = Engine(_feeder().build(), engine.scenario)
    restored.restore(raw)
    restored.step_control({"kind": "hold"})
    engine.step_control({"kind": "hold"})
    assert restored.time_s == pytest.approx(engine.time_s)
    assert abs(sum(restored.net.loads[0].p_w.values()) - sum(engine.net.loads[0].p_w.values())) < 1e-6


def test_ac_opf_tracks_load_and_dc_limit_changes_optimum():
    result = ac_opf_two_bus(0.5, 0.1, 0.25, cost=20, pmax=1)
    assert result["success"]
    assert result["pg_pu"] == pytest.approx(0.5, abs=1e-3)
    loose = dc_opf_network(
        n_bus=3,
        slack=0,
        lines=[(0, 1, 0.1, 2.0), (2, 1, 0.1, 2.0)],
        generators=[{"bus": 0, "cost": 10, "pmin": 0, "pmax": 2}, {"bus": 2, "cost": 40, "pmin": 0, "pmax": 2}],
        loads={1: 1.0},
    )
    tight = dc_opf_network(
        n_bus=3,
        slack=0,
        lines=[(0, 1, 0.1, 0.2), (2, 1, 0.1, 2.0)],
        generators=[{"bus": 0, "cost": 10, "pmin": 0, "pmax": 2}, {"bus": 2, "cost": 40, "pmin": 0, "pmax": 2}],
        loads={1: 1.0},
    )
    blocked = dc_opf_network(
        n_bus=3,
        slack=0,
        lines=[(0, 1, 0.1, 0.1), (2, 1, 0.1, 0.1)],
        generators=[{"bus": 0, "cost": 10, "pmin": 0, "pmax": 2}, {"bus": 2, "cost": 40, "pmin": 0, "pmax": 2}],
        loads={1: 1.0},
    )
    assert loose["success"] and tight["success"] and not blocked["success"]
    assert tight["generation_pu"][0] == pytest.approx(0.2, abs=1e-4)
    assert loose["generation_pu"][0] > tight["generation_pu"][0]


def test_empty_verdict_is_not_a_pass_and_interval_must_be_positive():
    assert _verdict([], []) == "incomplete"
    with pytest.raises(ValueError):
        Scenario(id="zero", control_interval_s=0)


def test_service_log_matches_reported_energy():
    engine = Engine(_feeder().build(), Scenario(id="energy", horizon_s=120, settle_s=600))
    engine.reset()
    record = engine.step_control({"kind": "hold"})
    assert energy_from_service_log([record]) == pytest.approx(record["energy_not_served_wh"])


def test_industrial_plant_solves_on_both_voltage_levels():
    result = solve_network(build_industrial_plant())
    assert result.status == "converged"
    energized = [item for item in result.voltages if item.energized and item.phase == "A"]
    assert energized
    assert all(0.9 <= abs(item.complex) / item.v_base_ln_v <= 1.1 for item in energized)


def test_imported_industrial_dss_and_symmetric_newton(tmp_path):
    from power_grid_env.conventions import ll_to_ln
    from power_grid_env.preflight import preflight
    from power_grid_env.reference import receiving_voltage_pq
    from power_grid_env.schema_export import export_schemas
    from power_grid_env.symmetric_ac import newton_power_flow, series_ybus

    network, report = import_network(
        Path("cases/industrial/plant/plant.dss"),
        Path("cases/industrial/plant/pge-import.yaml"),
    )
    assert network is not None, report["errors"]
    assert len(network.transformers) == 2
    cable = next(line for line in network.lines if line.id == "cable")
    assert cable.r_ohm[0][0] == pytest.approx(0.2)
    assert cable.x_ohm[0][0] == pytest.approx(0.3)
    assert all(row["status"] != "unsupported" for row in report["commands"])
    assert {level.nominal_ll_v for level in network.voltage_levels} >= {110_000.0, 10_000.0, 400.0}
    checked = preflight(network, task="power_flow")
    assert checked["ok"]
    assert checked["initial_solve"]["status"] == "converged"
    export_schemas(tmp_path)
    schema = json.loads((tmp_path / "network.schema.json").read_text(encoding="utf-8"))
    assert "s_base_va" in schema["properties"]

    impedance = 0.25 + 0.5j
    power = 300_000 + 100_000j
    expected, _low = receiving_voltage_pq(ll_to_ln(12_470), impedance, power)
    solved = newton_power_flow(
        series_ybus(impedance),
        np.array([0.0, -power.real]),
        np.array([0.0, -power.imag]),
        v_slack=ll_to_ln(12_470),
    )
    assert solved["converged"]
    assert abs(solved["voltage"][1] - abs(expected)) / ll_to_ln(12_470) < 1e-5


def test_linecode_impedance_scales_with_length(tmp_path):
    dss = tmp_path / "net.dss"
    dss.write_text(
        "\n".join(
            [
                "Clear",
                "New Circuit.t basekv=10 pu=1 phases=3 bus1=src",
                "Edit Vsource.source r1=0.1 x1=0.4",
                "New LineCode.cab nphases=3 r1=0.2 x1=0.4 units=km",
                "New Line.feeder bus1=src bus2=end phases=3 length=500 units=m linecode=cab",
                "New Load.end bus1=end phases=3 conn=wye kv=10 kw=100 kvar=0",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "pge-import.yaml").write_text(
        "\n".join(
            [
                "schema_version: power-grid-env.import/v1",
                "network_id: linecode_case",
                "name: linecode",
                "s_base_va: 1000000",
                "frequency_hz: 50",
                "impedance_interpretation: diagonal_from_positive_sequence",
                "grounding:",
                "  Load.end: solid",
                "lines:",
                "  Line.feeder:",
                "    rating_a: 200",
            ]
        ),
        encoding="utf-8",
    )
    network, report = import_network(dss, tmp_path / "pge-import.yaml")
    assert network is not None, report["errors"]
    assert network.lines[0].r_ohm[0][0] == pytest.approx(0.1)
    assert network.lines[0].x_ohm[0][0] == pytest.approx(0.2)
    assert network.lines[0].r_ohm[0][1] == 0.0


def test_fingerprint_tracks_the_controller_command():
    network = _feeder().build()
    scenario = Scenario(id="fingerprint", horizon_s=60, settle_s=600)
    first = experiment_fingerprint(scenario, {"command": ["noop"]}, network)
    second = experiment_fingerprint(scenario, {"command": ["other"]}, network)
    assert first != second
    assert experiment_fingerprint(scenario, {"command": ["noop"]}, network) == first


def test_two_machine_equilibrium_and_benchmark_is_finite():
    equilibrium = simulate_two_machine(pm=0.4, emf=1.05, reactance=0.3, inertia_s=4.0, disturbance_rad=0.0, t_end_s=0.05, dt_s=0.01)
    assert max(abs(value) for value in equilibrium["equilibrium_residual"]) < 1e-8
    timing = time_two_bus(repeats=2)
    assert math.isfinite(timing["p50_s"]) and timing["p50_s"] > 0
