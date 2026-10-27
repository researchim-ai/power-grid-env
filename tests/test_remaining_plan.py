"""Оставшиеся промышленные куски: Engine QSTS, инкремент нагрузок, изоляция окружения."""

import os
import sys
from pathlib import Path

import pytest

from power_grid_env.build import NetBuilder
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine


def _two_bus():
    builder = NetBuilder("inc", "inc", 10.0)
    builder.node("src")
    builder.node("ld")
    builder.source("src", 0.01, 0.05)
    builder.line_z1("src", "ld", 0.2 + 0.4j, line_id="line")
    builder.load("ld", 100_000.0, 20_000.0, load_id="load")
    return builder.build()


def test_ten_thousand_bus_nonconvergence_is_past_the_power_limit():
    from power_grid_env.benchmarks import RADIAL_SEGMENT_Z, radial_pq_limit, time_radial

    assert radial_pq_limit(2054)["exists"] is True
    assert radial_pq_limit(2055)["exists"] is False
    matched = time_radial(1000)
    assert matched["status"] == "converged"
    assert matched["min_voltage_pu"] == pytest.approx(matched["nose_voltage_pu"], abs=1e-6)
    nose = time_radial(2054)
    assert nose["status"] == "converged"
    assert nose["min_voltage_pu"] == pytest.approx(nose["nose_voltage_pu"], abs=1e-5)
    assert nose["pq_setpoint_error_pu"] <= 1e-5
    collapsed = time_radial(10000)
    assert collapsed["pq_solution_exists"] is False
    assert collapsed["status"] == "numerically_not_converged"
    assert collapsed["opendss_reported_converged"] is True
    assert collapsed["pq_setpoint_error_pu"] > 1e-5
    same_impedance = time_radial(10000, segment_z=RADIAL_SEGMENT_Z * (999 / 9999))
    assert same_impedance["pq_solution_exists"] is True
    assert same_impedance["status"] == "converged"
    assert same_impedance["min_voltage_pu"] == pytest.approx(radial_pq_limit(1000)["high_voltage_pu"], abs=1e-6)


def test_separate_q_profile_does_not_follow_p():
    network = _two_bus()
    engine = Engine(
        network,
        Scenario(
            id="pq",
            horizon_s=120,
            control_interval_s=60,
            settle_s=10_000,
            load_profile={"load": [[0.0, 1.0, 0.5], [60.0, 2.0, 0.25]]},
        ),
    )
    engine.reset()
    engine.step_control({"kind": "hold"})
    load = engine.net.loads[0]
    assert sum(load.p_w.values()) == pytest.approx(200_000.0)
    assert sum(load.q_var.values()) == pytest.approx(5_000.0)


def test_trace_limit_drops_old_steps_and_keeps_time():
    engine = Engine(
        _two_bus(),
        Scenario(id="trace", horizon_s=180, control_interval_s=60, settle_s=10_000, trace_limit=1),
    )
    engine.reset()
    engine.step_control({"kind": "hold"})
    engine.step_control({"kind": "hold"})
    assert len(engine.trace) == 1
    assert engine.dropped_trace_steps == 1
    assert engine.time_s == pytest.approx(120.0)
    raw = engine.snapshot()
    restored = Engine(_two_bus(), engine.scenario)
    restored.restore(raw)
    assert restored.dropped_trace_steps == 1


def test_incremental_load_edit_matches_a_cold_solve():
    from power_grid_env.backend_opendss import solve_loads_incrementally, solve_network

    base = _two_bus()
    edited = base.model_copy(deep=True)
    for load in edited.loads:
        for phase in load.p_w:
            load.p_w[phase] *= 1.5
            load.q_var[phase] *= 0.8
    cold = solve_network(edited)
    warm = solve_loads_incrementally(base, edited)
    assert cold.status == "converged"
    assert warm.status == "converged"
    cold_v = {(item.bus_id, item.phase): abs(item.complex) / item.v_base_ln_v for item in cold.voltages if item.v_base_ln_v}
    warm_v = {(item.bus_id, item.phase): abs(item.complex) / item.v_base_ln_v for item in warm.voltages if item.v_base_ln_v}
    assert cold_v.keys() == warm_v.keys()
    assert max(abs(cold_v[key] - warm_v[key]) for key in cold_v) < 1e-5


def test_isolated_controller_does_not_inherit_parent_secrets(tmp_path: Path):
    from power_grid_env.controllers import SubprocessController

    script = tmp_path / "leak.py"
    script.write_text(
        "import os, sys\n"
        "secret = os.environ.get('SECRET_CANARY', '')\n"
        "sys.stdout.write('{\"ok\": true, \"secret\": \"%s\"}\\n' % secret)\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    os.environ["SECRET_CANARY"] = "hidden-value"
    try:
        controller = SubprocessController([sys.executable, str(script)], timeout_s=2.0, isolated=True)
        result = controller._request({"op": "reset"})
        controller.close()
    finally:
        os.environ.pop("SECRET_CANARY", None)
    assert result["outcome"] == "ok"
    assert result["data"].get("secret") == ""
    assert controller.scratch is not None
    assert "hidden-value" not in Path(controller.scratch).as_posix()


def test_engine_qsts_matches_the_direct_profile_solve():
    master = Path(
        "data/external/raw/smart-ds/v1.0/2018/SFO/P1U/opendss/"
        "p1uhs0_1247/p1uhs0_1247--p1udt104/Master.dss"
    )
    if not master.exists():
        pytest.skip("локальный фидер SMART-DS отсутствует")
    from benchmarks.external.smart_ds_qsts import engine_window

    payload = engine_window(steps=2)
    assert payload["verdict"] == "pass", payload
    assert payload["max_voltage_error_pu"] < 1e-5


def test_rating_mutation_fails_the_independent_audit():
    path = Path("data/external/raw/pglib-opf/pglib_opf_case3_lmbd.m")
    if not path.exists():
        pytest.skip("локальный PGLib отсутствует")
    import numpy as np
    from dataclasses import replace

    from benchmarks.external.pglib_opf import load_case
    from power_grid_env.network_opf import OpfCase, audit_feasibility, solve_ac_opf

    case = load_case(path)
    solved = solve_ac_opf(case)
    assert solved["success"]
    tight = OpfCase(
        name=case.name,
        base_mva=case.base_mva,
        buses=case.buses,
        generators=case.generators,
        branches=[replace(branch, rate_a=0.001) for branch in case.branches],
        published_cost=case.published_cost,
    )
    audit = audit_feasibility(
        tight,
        np.array(solved["vm_pu"]),
        np.radians(solved["va_deg"]),
        np.array(solved["pg_mw"]),
        np.array(solved["qg_mvar"]),
    )
    assert audit["flows_within_rating"] is False
    assert audit["feasible"] is False


def test_rms_release_is_not_andes():
    from power_grid_env.dynamics import release_status

    status = release_status()
    assert status["industrial_rms_qualified"] is False
    assert status["andes_integrated"] is False
