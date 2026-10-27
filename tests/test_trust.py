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


def test_delayed_tap_and_midpoint_checkpoint_match():
    builder = NetBuilder("ltc", "РПН", 20.0)
    builder.node("hv", 20.0)
    builder.node("lv", 0.4)
    builder.source("hv", 0.05, 0.2)
    builder.transformer(
        "hv",
        "lv",
        vector_group="Dyn11",
        kva=160,
        hv_kv=20,
        lv_kv=0.4,
        x_pct=4,
        r_pct=1,
        hv_ground="not_applicable",
        lv_ground="solid",
        transformer_id="t",
    )
    builder.load("lv", 20_000, 5_000, load_id="load")
    network = builder.build()
    network.transformers[0].ltc = __import__(
        "power_grid_env.model", fromlist=["LTCSpec"]
    ).LTCSpec(
        controlled_winding=1,
        v_target_pu=1.2,
        deadband_pu=0.01,
        tap_min_pu=0.9,
        tap_max_pu=1.1,
        tap_step_pu=0.01,
        delay_s=5.0,
    )
    engine = Engine(network, Scenario(id="ltc", horizon_s=30, settle_s=600, control_interval_s=30))
    engine.reset()
    assert engine.net.transformers[0].windings[1].tap_pu == pytest.approx(1.0)
    engine.advance_to(4.0)
    assert engine.net.transformers[0].windings[1].tap_pu == pytest.approx(1.0)
    snapshot = json.loads(json.dumps(engine.snapshot()))
    engine.advance_to(5.0)
    assert engine.net.transformers[0].windings[1].tap_pu == pytest.approx(1.01)
    restored = Engine(network.model_copy(deep=True), engine.scenario)
    restored.restore(snapshot)
    restored.advance_to(5.0)
    assert restored.time_s == pytest.approx(engine.time_s)
    assert restored.net.transformers[0].windings[1].tap_pu == pytest.approx(1.01)


def test_execution_rechecks_maintenance_and_uv_uses_its_node():
    builder = NetBuilder("prot", "Защита", 12.47)
    builder.node("src")
    builder.node("end")
    builder.source("src", 0.02, 0.08)
    builder.switch("src", "end", switch_id="sw", operate_time_s=1.0)
    builder.load("end", 50_000, 0.0, load_id="load")
    engine = Engine(
        builder.build(),
        Scenario(
            id="prot",
            horizon_s=30,
            settle_s=600,
            control_interval_s=30,
            protection=[
                {
                    "id": "uv",
                    "kind": "undervoltage",
                    "equipment_id": "sw",
                    "measured_node_id": "end",
                    "pickup": 0.9,
                    "time_multiplier_s": 0.5,
                    "breaker_delay_s": 0.0,
                    "max_shots": 1,
                }
            ],
        ),
    )
    engine.reset()
    engine.net.switches[0].closed = False
    engine._resolve()
    engine.submit({"kind": "switch", "equipment_id": "sw", "command": "close"})
    engine.net.switches[0].tagged_for_maintenance = True
    engine.advance_to(1.0)
    assert engine.net.switches[0].closed is False
    assert engine.execution_log[-1]["reason"] == "maintenance_tag"
    engine.advance_to(1.5)
    assert engine.relays[0].shots == 1
    assert engine.relays[0].lockout is True


def test_equal_time_switch_order_follows_submission():
    builder = NetBuilder("order", "Порядок", 12.47)
    builder.node("src")
    builder.node("a")
    builder.node("b")
    builder.source("src", 0.02, 0.08)
    builder.switch("src", "a", switch_id="a", closed=False, operate_time_s=0.0)
    builder.switch("src", "b", switch_id="b", closed=False, operate_time_s=0.0)
    builder.load("a", 10_000, 0.0, load_id="la")
    builder.load("b", 10_000, 0.0, load_id="lb")
    engine = Engine(builder.build(), Scenario(id="order", horizon_s=10, settle_s=600, radiality=False))
    engine.reset()
    engine.submit({"kind": "switch", "equipment_id": "a", "command": "close"})
    engine.submit({"kind": "switch", "equipment_id": "b", "command": "close"})
    engine.advance_to(0.01)
    assert [item["equipment_id"] for item in engine.execution_log] == ["a", "b"]
    assert engine.net.switches[0].closed and engine.net.switches[1].closed


def test_exact_mode_rejects_unverified_winding_shift():
    from power_grid_env.preflight import preflight
    from power_grid_env.results import SolveOptions

    builder = NetBuilder("dyn5", "Dyn5", 20.0)
    builder.node("hv", 20.0)
    builder.node("lv", 0.4)
    builder.source("hv", 0.1, 0.4)
    builder.transformer(
        "hv",
        "lv",
        vector_group="Dyn5",
        kva=160,
        hv_kv=20,
        lv_kv=0.4,
        x_pct=4,
        r_pct=1,
        hv_ground="not_applicable",
        lv_ground="solid",
    )
    builder.load("lv", 10_000, 0.0, load_id="load")
    network = builder.build()
    network.transformers[0].windings[1].angle_deg = -150.0
    blocked = solve_network(network)
    assert blocked.status == "invalid_model"
    assert any("angle" in item for item in blocked.warnings)
    allowed = solve_network(network, SolveOptions(approximate_phase_shift=True))
    assert allowed.status == "converged"
    assert preflight(network, task="power_flow", solve_initial=False)["ok"] is False
    assert preflight(network, task="power_flow", solve_initial=False, approximate_phase_shift=True)["ok"] is True


def test_fuse_is_outside_power_flow_and_blocks_protection(tmp_path: Path):
    from power_grid_env.preflight import preflight

    (tmp_path / "net.dss").write_text(
        "\n".join(
            [
                "Clear",
                "New Circuit.t basekv=10 pu=1 phases=3 bus1=a",
                "Edit Vsource.source r1=0.1 x1=0.1",
                "New Line.l bus1=a bus2=b phases=3 length=1 units=km r1=0.2 x1=0.3 r0=0.6 x0=0.9",
                "New Load.b bus1=b phases=3 conn=wye kv=10 kw=10 kvar=0",
                "New Fuse.f monitoredobj=Line.l switchedobj=Line.l",
            ]
        ),
        encoding="utf-8",
    )
    network, report = import_network(tmp_path / "net.dss")
    assert network is not None, report["errors"]
    fuse = next(row for row in report["commands"] if row["command"] == "Fuse.f")
    assert fuse["power_flow"] == "not_in_admittance"
    assert fuse["protection"] == "unsupported"
    power_flow = preflight(network, task="power_flow", solve_initial=False)
    assert power_flow["ok"] is True
    assert any("Fuse.f" in item for item in power_flow["warnings"])
    protection = preflight(network, task="protection", solve_initial=False)
    assert protection["ok"] is False


def test_profiled_outage_counts_the_raised_demand():
    builder = NetBuilder("outage", "Отключение", 12.47)
    builder.node("src")
    builder.node("end")
    builder.source("src", 0.02, 0.08)
    builder.switch("src", "end", switch_id="sw", closed=False)
    builder.load("end", 100_000, 0.0, load_id="load")
    engine = Engine(
        builder.build(),
        Scenario(
            id="outage",
            horizon_s=60,
            settle_s=600,
            control_interval_s=60,
            load_profile={"load": [[0, 1.0], [30, 2.0]]},
        ),
    )
    engine.reset()
    record = engine.step_control({"kind": "hold"})
    assert record["energy_not_served_wh"] == pytest.approx(2500.0)
    assert energy_from_service_log([record]) == pytest.approx(2500.0)
    assert record["service_rows"][0]["nameplate_w"] == pytest.approx(100_000)


def test_partial_controller_line_cannot_outlive_the_deadline(tmp_path: Path):
    import sys
    import time

    from power_grid_env.controllers import SubprocessController

    script = tmp_path / "partial.py"
    script.write_text(
        "import sys, time\nsys.stdout.write('{')\nsys.stdout.flush()\ntime.sleep(5)\n",
        encoding="utf-8",
    )
    controller = SubprocessController([sys.executable, str(script)], timeout_s=0.3)
    started = time.monotonic()
    result = controller._request({"op": "act"})
    elapsed = time.monotonic() - started
    assert result["outcome"] == "timeout"
    assert elapsed < 1.2
    assert controller.process.poll() is not None


def test_controller_stderr_flood_still_returns_the_frame(tmp_path: Path):
    import sys

    from power_grid_env.controllers import SubprocessController

    script = tmp_path / "flood.py"
    script.write_text(
        "import sys\nsys.stderr.write('x' * 200000)\nsys.stderr.flush()\n"
        "sys.stdout.write('{\"ok\": true}\\n')\nsys.stdout.flush()\n",
        encoding="utf-8",
    )
    controller = SubprocessController([sys.executable, str(script)], timeout_s=2.0)
    result = controller._request({"op": "reset"})
    assert result["outcome"] == "ok"
    controller.close()


def test_controller_rejects_a_json_array(tmp_path: Path):
    import sys

    from power_grid_env.controllers import SubprocessController

    script = tmp_path / "array.py"
    script.write_text("import sys\nsys.stdout.write('[]\\n')\nsys.stdout.flush()\n", encoding="utf-8")
    controller = SubprocessController([sys.executable, str(script)], timeout_s=1.0)
    result = controller._request({"op": "act"})
    assert result["outcome"] == "invalid_action"
    assert controller.process.poll() is not None


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


def _delayed_ltc_network():
    from power_grid_env.model import LTCSpec

    builder = NetBuilder("ltc", "РПН", 20.0)
    builder.node("hv", 20.0)
    builder.node("lv", 0.4)
    builder.source("hv", 0.05, 0.2)
    builder.transformer(
        "hv",
        "lv",
        vector_group="Dyn11",
        kva=160,
        hv_kv=20,
        lv_kv=0.4,
        x_pct=4,
        r_pct=1,
        hv_ground="not_applicable",
        lv_ground="solid",
        transformer_id="t",
    )
    builder.load("lv", 20_000, 5_000, load_id="load")
    network = builder.build()
    network.transformers[0].ltc = LTCSpec(
        controlled_winding=1,
        v_target_pu=1.2,
        deadband_pu=0.01,
        tap_min_pu=0.9,
        tap_max_pu=1.1,
        tap_step_pu=0.01,
        delay_s=5.0,
    )
    return network


def test_direct_and_process_backends_share_the_tap_timeline():
    from power_grid_env.backend_process import ProcessBackend

    scenario = Scenario(id="parity", horizon_s=30, settle_s=600, control_interval_s=30)
    direct = Engine(_delayed_ltc_network(), scenario)
    direct.reset()
    backend = ProcessBackend(timeout_s=60)
    try:
        process = Engine(_delayed_ltc_network(), scenario, solve=backend.solve)
        process.reset()
        assert direct.net.transformers[0].windings[1].tap_pu == pytest.approx(1.0)
        assert process.net.transformers[0].windings[1].tap_pu == pytest.approx(1.0)
        direct.advance_to(5.0)
        process.advance_to(5.0)
        assert process.net.transformers[0].windings[1].tap_pu == pytest.approx(
            direct.net.transformers[0].windings[1].tap_pu
        )
        assert process.net.transformers[0].windings[1].tap_pu == pytest.approx(1.01)
    finally:
        backend.close()


def test_parallel_infeed_is_rejected_and_storage_respects_energy():
    from power_grid_env.industrial import build_industrial_plant

    engine = Engine(build_industrial_plant(), Scenario(id="ats", horizon_s=60, settle_s=600))
    engine.reset()
    refused = engine.submit({"kind": "switch", "equipment_id": "ats", "command": "close"})
    assert refused["accepted"] is False
    assert refused["reason"] == "parallel_infeed"
    engine.net.switches[0].closed = False
    engine._resolve()
    transferred = engine.submit({"kind": "switch", "equipment_id": "ats", "command": "close"})
    assert transferred["accepted"] is True
    builder = NetBuilder("bess", "Накопитель", 0.4)
    builder.node("src", 0.4)
    builder.source("src", 0.01, 0.04)
    builder.storage("src", e_j=1_000_000, soc=0.5, p_w=0.0, s_rated_va=10_000, storage_id="storage")
    stored = Engine(builder.build(), Scenario(id="bess", horizon_s=60, settle_s=600, control_interval_s=60))
    stored.reset()
    stored.step_control({"kind": "setpoint", "equipment_id": "storage", "value": 1_000_000_000, "unit": "W"})
    # Запрошен 1 ГВт. За минуту можно отдать только (0.5−0.1)·1 МДж с учётом КПД, SoC садится на пол.
    assert stored.soc["storage"] == pytest.approx(stored.net.storages[0].soc_min)
    assert stored.net.storages[0].p_w <= stored.net.storages[0].s_rated_va


def test_installed_case_does_not_search_for_a_repository(tmp_path, monkeypatch):
    from power_grid_env.paths import project_dir
    from power_grid_env.workflow import run_case

    monkeypatch.setenv("POWER_GRID_PROJECT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert project_dir() == tmp_path.resolve()
    model = tmp_path / "model.json"
    model.write_text(_feeder().build().model_dump_json(), encoding="utf-8")
    result = run_case(model, tmp_path / "out")
    assert result["ok"] is True
    assert result["resumed_time_s"] == result["time_s"]
    assert "power-grid-env не найден" not in str(result)


def test_fingerprint_tracks_the_controller_command(tmp_path: Path):
    network = _feeder().build()
    scenario = Scenario(id="fingerprint", horizon_s=60, settle_s=600)
    first = experiment_fingerprint(scenario, {"command": ["noop"]}, network)
    second = experiment_fingerprint(scenario, {"command": ["other"]}, network)
    assert first != second
    assert experiment_fingerprint(scenario, {"command": ["noop"]}, network) == first
    script = tmp_path / "controller.py"
    script.write_text("print(1)\n", encoding="utf-8")
    before = experiment_fingerprint(scenario, {"command": ["python3", str(script)]}, network)
    script.write_text("print(2)\n", encoding="utf-8")
    after = experiment_fingerprint(scenario, {"command": ["python3", str(script)]}, network)
    assert before != after
    helper = tmp_path / "helper.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    script.write_text("import helper\n", encoding="utf-8")
    with_helper = experiment_fingerprint(scenario, {"command": ["python3", str(script)]}, network)
    helper.write_text("VALUE = 2\n", encoding="utf-8")
    changed_helper = experiment_fingerprint(scenario, {"command": ["python3", str(script)]}, network)
    assert with_helper != changed_helper


def test_attempts_stay_immutable_and_idempotency_does_not_duplicate(tmp_path: Path):
    from power_grid_env.runner import run_suite

    model = tmp_path / "model.json"
    model.write_text(_feeder().build().model_dump_json(), encoding="utf-8")
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "\n".join(
            [
                "id: tiny",
                "title: tiny",
                "task: power_flow",
                "scenarios:",
                "  - id: one",
                f"    model: {model}",
                "    baseline: {policy: hold}",
                "    candidate: {policy: hold}",
                "    scenario:",
                "      id: one",
                "      horizon_s: 60",
                "      control_interval_s: 60",
                "      settle_s: 600",
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    first = run_suite(suite, out, use_process_backend=False, idempotency_key="run-1")
    assert first["verdict"] == "pass"
    again = run_suite(suite, out, use_process_backend=False, idempotency_key="run-1")
    assert again["verdict"] == "pass"
    attempts = list((out / "one" / "baseline" / "attempts").iterdir())
    assert len(attempts) == 1
    kept = attempts[0]
    (kept / "metrics.json").write_text("{", encoding="utf-8")
    (out / "one" / "baseline" / "latest.json").write_text("{", encoding="utf-8")
    resumed = run_suite(suite, out, use_process_backend=False, resume=True)
    assert resumed["verdict"] == "pass"
    assert kept.exists()
    assert len(list((out / "one" / "baseline" / "attempts").iterdir())) == 2
    suite.write_text(suite.read_text(encoding="utf-8").replace("horizon_s: 60", "horizon_s: 120"), encoding="utf-8")
    with pytest.raises(ValueError, match="другим отпечатком"):
        run_suite(suite, out, use_process_backend=False, idempotency_key="run-1")


def test_two_machine_equilibrium_and_benchmark_is_finite():
    equilibrium = simulate_two_machine(pm=0.4, emf=1.05, reactance=0.3, inertia_s=4.0, disturbance_rad=0.0, t_end_s=0.05, dt_s=0.01)
    assert max(abs(value) for value in equilibrium["equilibrium_residual"]) < 1e-8
    timing = time_two_bus(repeats=2)
    assert math.isfinite(timing["p50_s"]) and timing["p50_s"] > 0
