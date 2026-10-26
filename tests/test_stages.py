"""Проверки этапов 2–9: импорт, эталон, пилот, OPF, динамика, EMT, просмотр."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from power_grid_env.analysis import ac_opf_network, n1_transformer_outages
from power_grid_env.benchmarks import time_radial
from power_grid_env.dynamics import simulate_smib_with_controls
from power_grid_env.emt import rlc_exact_underdamped, rlc_step_difference, rlc_trapezoidal
from power_grid_env.heuristic import choose_action
from power_grid_env.importer import import_network
from power_grid_env.llm_tools import validate_tool_call
from power_grid_env.preflight import preflight
from power_grid_env.schema_export import export_schemas
from power_grid_env.study import compare_main_path, run_pilot
from power_grid_env.tasks import task_ids
from power_grid_env.viewer import viewer_bundle, write_viewer

ROOT = Path("cases/industrial/plant")


def _plant():
    network, report = import_network(ROOT / "plant.dss", ROOT / "pge-import.yaml")
    assert network is not None, report["errors"]
    return network, report


def test_plant_reference_pilot_and_devices(tmp_path):
    network, report = _plant()
    assert len(report["sources"]) == 2
    assert report["load_profiles"]["plant"][1] == [1800.0, 1.5]
    assert any(item.id == "bank" for item in network.shunts)
    assert any(not switch.closed for switch in network.switches if switch.id == "ats")
    compared = compare_main_path(network)
    assert compared["within_tolerance"], compared
    pilot = run_pilot(network)
    assert pilot["solve_status"]
    assert pilot["time_s"] == pilot["resumed_time_s"]
    assert "fault_r_ohm" not in json.dumps(pilot["trace"])
    outages = n1_transformer_outages(network)
    assert {item["outage"] for item in outages} == {"t110", "t04"}
    assert all("status" in item for item in outages)
    bundle = viewer_bundle(network.model_dump(mode="json"), pilot["trace"], 0.9, 1.1)
    assert any(item["id"] == "t110" and item["group"] == "YNyn0" for item in bundle["transformers"])
    assert {item["kind"] for item in bundle["devices"]} >= {"load", "generator", "storage", "shunt", "inverter", "source"}
    page = tmp_path / "viewer.html"
    write_viewer(page, bundle)
    text = page.read_text(encoding="utf-8")
    assert "sld-breaker-closed" in text
    assert "sld-transformer-2" in text
    assert "polyline" in text
    assert "Принято:" in text


def test_matrix_redirect_and_three_winding(tmp_path):
    dss = tmp_path / "net.dss"
    dss.write_text(
        "\n".join(
            [
                "Clear",
                "New Circuit.m basekv=10 pu=1 phases=3 bus1=a",
                "Edit Vsource.source r1=0.1 x1=0.3",
                "New Line.l bus1=a bus2=b phases=4 length=2 units=km",
                "~ rmatrix=[0.2 | 0.02 0.2 | 0.02 0.02 0.2 | 0.01 0.01 0.01 0.3]",
                "~ xmatrix=[0.4 | 0.04 0.4 | 0.04 0.04 0.4 | 0.02 0.02 0.02 0.5]",
                "New Load.one bus1=b.1 phases=1 conn=wye kv=5.774 kw=10 kvar=0",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "side.yaml").write_text(
        "\n".join(
            [
                "schema_version: power-grid-env.import/v1",
                "network_id: matrix",
                "name: matrix",
                "s_base_va: 1000000",
                "frequency_hz: 50",
                "impedance_interpretation: explicit_phase_matrix",
                "grounding:",
                "  Load.one: solid",
                "lines:",
                "  Line.l:",
                "    rating_a: 100",
            ]
        ),
        encoding="utf-8",
    )
    network, report = import_network(dss, tmp_path / "side.yaml")
    assert network is not None, report["errors"]
    assert network.lines[0].r_ohm[0][0] == pytest.approx(0.4)
    assert network.lines[0].r_ohm[0][1] == pytest.approx(0.04)
    assert "N" in network.lines[0].phases

    first = tmp_path / "loop_a.dss"
    second = tmp_path / "loop_b.dss"
    first.write_text("Redirect loop_b.dss\n", encoding="utf-8")
    second.write_text("Redirect loop_a.dss\n", encoding="utf-8")
    _none, loop_report = import_network(first, tmp_path / "side.yaml")
    assert _none is None
    assert any("цикл" in item for item in loop_report["errors"])

    three = tmp_path / "three.dss"
    three.write_text(
        "\n".join(
            [
                "Clear",
                "New Circuit.t basekv=110 pu=1 phases=3 bus1=hv",
                "Edit Vsource.source r1=0.5 x1=2",
                "New Transformer.t3 phases=3 windings=3 xhl=10 xht=12 xlt=8 %loadloss=1",
                "~ wdg=1 bus=hv conn=wye kv=110 kva=10000",
                "~ wdg=2 bus=mv conn=wye kv=10 kva=10000",
                "~ wdg=3 bus=tv conn=wye kv=10 kva=10000",
                "New Load.mv bus1=mv phases=3 conn=wye kv=10 kw=100 kvar=0",
                "New RegControl.tap transformer=t3 winding=2",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "three.yaml").write_text(
        "\n".join(
            [
                "schema_version: power-grid-env.import/v1",
                "network_id: three",
                "name: three",
                "s_base_va: 100000000",
                "frequency_hz: 50",
                "impedance_interpretation: diagonal_from_positive_sequence",
                "grounding:",
                "  Load.mv: solid",
                "transformers:",
                "  Transformer.t3:",
                "    vector_group: YNyn0yn0",
                "    grounding: [solid, solid, solid]",
                "regulators:",
                "  RegControl.tap:",
                "    winding: 1",
                "    v_target_pu: 1.0",
                "    deadband_pu: 0.01",
                "    tap_min_pu: 0.9",
                "    tap_max_pu: 1.1",
                "    tap_step_pu: 0.0125",
            ]
        ),
        encoding="utf-8",
    )
    built, three_report = import_network(three, tmp_path / "three.yaml")
    assert built is not None, three_report["errors"]
    assert len(built.transformers[0].windings) == 3
    assert built.transformers[0].ltc is not None
    checked = preflight(built, task="power_flow")
    assert checked["initial_solve"]["status"] == "converged"


def test_negative_resistance_is_rejected():
    network, _report = _plant()
    broken = network.model_copy(deep=True)
    broken.lines[0].r_ohm[0][0] = -1.0
    checked = preflight(broken, task="power_flow", solve_initial=False)
    assert any("отрицательное" in item for item in checked["errors"])


def test_opf_dynamics_emt_tasks_and_timing(tmp_path):
    blocked = ac_opf_network(load_p=0.5, load_q=0.1, x_pu=0.2, flow_limit=0.2, pmax=1.0)
    allowed = ac_opf_network(load_p=0.5, load_q=0.1, x_pu=0.2, flow_limit=1.0, pmax=1.0)
    assert blocked["status"] == "infeasible"
    assert allowed["status"] == "optimal"
    assert allowed["pg_pu"] == pytest.approx(0.5, abs=1e-4)
    quiet = simulate_smib_with_controls(pm=0.4, e_pu=1.05, v_pu=1.0, x_pu=0.3, inertia_s=4.0, t_end_s=0.05)
    assert max(abs(value) for value in quiet["equilibrium_residual"]) < 1e-8
    dipped = simulate_smib_with_controls(
        pm=0.4, e_pu=1.05, v_pu=1.0, x_pu=0.3, inertia_s=4.0, t_end_s=0.3, voltage_scale=0.9
    )
    assert float(dipped["field"][-1]) > 1.05
    time_s = np.linspace(0.0, 0.002, 21)
    exact = rlc_exact_underdamped(10.0, 2.0, 0.01, 1e-4, time_s)
    numeric = rlc_trapezoidal(10.0, 2.0, 0.01, 1e-4, 1e-5, 200)
    assert abs(numeric[20] - exact[2]) < 0.05
    assert rlc_step_difference(10.0, 2.0, 0.01, 1e-4, 1e-4, 20) > rlc_step_difference(10.0, 2.0, 0.01, 1e-4, 5e-5, 40)
    assert set(task_ids()) >= {"restoration", "volt_var", "bess_dispatch", "contingency_screening", "diagnosis"}
    assert validate_tool_call("simulate_plan", {"commands": []})["ok"]
    assert not validate_tool_call("simulate_plan", {})["ok"]
    assert choose_action({"switches": [], "action_mask": []}, {"lines": [], "switches": []}, respect_rating=True)["kind"] == "hold"
    timing = time_radial(6, repeats=1)
    assert timing["converged"] == 1.0
    assert timing["p50_s"] > 0
    export_schemas(tmp_path)
    schema = json.loads((tmp_path / "scenario.schema.json").read_text(encoding="utf-8"))
    assert "horizon_s" in schema["properties"]
