"""Производные режимы IEEE: множитель нагрузки, замороженные регуляторы, размыкание Sw1.

Исходный файл не меняется. Рабочая копия получает команды до первого Solve,
чтобы дискретная отпайка не наследовалась от другого режима. Допуски те же,
что у снимка: 1e-5 о.е. и 1e-4 рад.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import yaml

from benchmarks.external._common import RAW, sha256_file, write_json
from benchmarks.external.compare import ANGLE_RAD, VOLTAGE_PU, compare_buses
from benchmarks.external.extract_native_reference import _prepare_workdir, _solve_tight

TAP_STEP = 0.00625
PACK = Path(__file__).resolve().parent / "packs" / "derived.yaml"


def run_pack(out: Path) -> dict[str, Any]:
    pack = yaml.safe_load(PACK.read_text(encoding="utf-8"))
    cases = []
    for case in pack["cases"]:
        master = RAW / "ieee-pes" / case["folder"] / case["master"]
        for scenario in case["scenarios"]:
            cases.append(_scenario(master, case, scenario))
    required = [item for item in cases if item["verdict"] != "not_run"]
    if any(item["verdict"] == "fail" for item in required):
        summary = "fail"
    elif required and all(item["verdict"] == "pass" for item in required):
        summary = "pass"
    else:
        summary = "inconclusive"
    payload = {
        "pack": pack["id"],
        "verdict": summary,
        "evidence": "derived_scenario",
        "thresholds": {"voltage_pu": VOLTAGE_PU, "angle_rad": ANGLE_RAD, "tap_step": TAP_STEP},
        "cases": cases,
    }
    write_json(out / "results.json", payload)
    return payload


def _scenario(master: Path, case: dict[str, Any], scenario: str) -> dict[str, Any]:
    loadmult = {"frozen": 1.0, "load_0_5": 0.5, "load_1_2": 1.2, "open_sw1": 1.0}[scenario]
    controls = scenario != "frozen"
    open_line = case.get("open_line") if scenario == "open_sw1" else None
    native = _native(master, loadmult=loadmult, controls=controls, open_line=open_line)
    imported = _imported(master, loadmult=loadmult, controls=controls, open_line=open_line)
    comparison = compare_buses(native, imported["rows"])
    tap_gap = _tap_gap(native.get("taps") or {}, imported["taps"])
    verdict = comparison["verdict"]
    if verdict == "pass" and tap_gap > TAP_STEP / 2:
        verdict = "fail"
    note = None
    if verdict == "fail" and tap_gap <= TAP_STEP / 2:
        note = "отпайки совпали; модуль напряжения вышел за 1e-5 о.е. Порог не сдвигался."
    elif verdict == "fail":
        note = "отпайка регулятора разошлась больше чем на полшага. Порог напряжения не сдвигался."
    return {
        "id": f"{case['id']}-{scenario}",
        "scenario": scenario,
        "load_multiplier": loadmult,
        "controls": "file" if controls else "off",
        "open_line": open_line,
        "verdict": verdict,
        "native_converged": native.get("converged"),
        "import_status": imported["status"],
        "max_voltage_error_pu": comparison["max_voltage_error_pu"],
        "max_angle_error_rad": comparison["max_angle_error_rad"],
        "max_tap_gap": tap_gap,
        "derived_sha256": native.get("derived_sha256"),
        "master_sha256": sha256_file(master),
        "evidence": "derived_scenario",
        "note": note,
    }


def _native(master: Path, *, loadmult: float, controls: bool, open_line: str | None) -> dict[str, Any]:
    from opendssdirect.OpenDSSDirect import OpenDSSDirect

    master = master.resolve()
    work = _prepare_workdir(master)
    target = work / master.name
    rewritten = _rewrite(target.read_text(encoding="utf-8", errors="replace"), loadmult, controls, open_line)
    target.write_text(rewritten, encoding="utf-8")
    session = OpenDSSDirect().NewContext()
    previous = Path.cwd()
    try:
        session.Text.Command("clear")
        session.Text.Command(f'compile "{target.resolve()}"')
        _solve_tight(session)
        buses = _buses(session)
        taps = _native_taps(session)
        return {
            "converged": bool(session.Solution.Converged()),
            "buses": buses,
            "taps": taps,
            "derived_sha256": hashlib.sha256(rewritten.encode("utf-8")).hexdigest(),
        }
    except Exception as exc:
        return {"converged": False, "error": str(exc), "buses": [], "taps": {}}
    finally:
        os.chdir(previous)


def _imported(master: Path, *, loadmult: float, controls: bool, open_line: str | None) -> dict[str, Any]:
    from power_grid_env.backend_opendss import solve_network
    from power_grid_env.importer import import_network
    from power_grid_env.results import SolveOptions
    from power_grid_env.topology import compile_topology

    network, report = import_network(master)
    if network is None:
        return {"status": "unsupported", "rows": [], "taps": {}, "errors": report.get("errors")}
    network = network.model_copy(deep=True)
    if loadmult != 1.0:
        for load in network.loads:
            load.p_w = {phase: value * loadmult for phase, value in load.p_w.items()}
            load.q_var = {phase: value * loadmult for phase, value in load.q_var.items()}
    if not controls:
        for transformer in network.transformers:
            transformer.ltc = None
    if open_line:
        network.lines = [line for line in network.lines if line.id.lower() != open_line.lower()]
        network.terminals = [item for item in network.terminals if item.equipment_id.lower() != open_line.lower()]
    solved = solve_network(network, SolveOptions(tolerance=1e-10, max_iterations=200, apply_regulators=controls))
    return {
        "status": solved.status,
        "rows": _rows(network, solved, compile_topology(network)),
        "taps": dict(solved.diagnostics.get("solved_taps") or {}),
    }


def _rewrite(text: str, loadmult: float, controls: bool, open_line: str | None) -> str:
    lines = []
    block = False
    for line in text.splitlines():
        if block:
            lines.append(line)
            if "*/" in line:
                block = False
            continue
        if "/*" in line and "*/" not in line:
            block = True
            lines.append(line)
            continue
        code = line.split("!")[0].split("//")[0].strip().lower()
        if code in {"solve", "calcv", "calcvoltagebases"}:
            lines.append("! derived: команда исходника перенесена в конец, после задания режима")
            continue
        lines.append(line)
    lines.append("! derived scenario, исходный файл не изменялся")
    lines.append(f"Set loadmult={loadmult:.8g}")
    if not controls:
        lines.append("Set controlmode=OFF")
    if open_line:
        lines.append(f"Line.{open_line}.enabled=no")
    lines.append("CalcVoltageBases")
    lines.append("Solve")
    return "\n".join(lines) + "\n"


def _buses(session) -> list[dict[str, Any]]:
    buses = []
    for name in session.Circuit.AllBusNames():
        session.Circuit.SetActiveBus(name)
        nodes = list(session.Bus.Nodes())
        pu = list(session.Bus.puVmagAngle())
        phases = []
        for index, node in enumerate(nodes):
            if index * 2 + 1 >= len(pu):
                break
            phases.append({"node": int(node), "pu": float(pu[index * 2]), "angle_deg": float(pu[index * 2 + 1])})
        buses.append({"name": name, "phases": phases})
    return buses


def _native_taps(session) -> dict[str, float]:
    taps = {}
    try:
        names = list(session.Transformers.AllNames())
    except Exception:
        return taps
    for name in names:
        try:
            session.Text.Command(f"Transformer.{name}.wdg=2")
            session.Text.Command(f"? Transformer.{name}.tap")
            taps[name] = float(str(session.Text.Result()).strip())
        except Exception:
            continue
    return taps


def _tap_gap(native: dict[str, float], imported: dict[str, float]) -> float:
    imported_index = {key.lower(): value for key, value in imported.items()}
    gap = 0.0
    for name, tap in native.items():
        other = imported_index.get(name.lower())
        if other is None:
            continue
        gap = max(gap, abs(tap - other))
    return gap


def _rows(network, solved, topology) -> list[dict[str, Any]]:
    import math

    phase_node = {"A": 1, "B": 2, "C": 3, "N": 4}
    rows = []
    for node_id, bus_id in topology.node_to_bus.items():
        for voltage in solved.voltages:
            if voltage.bus_id != bus_id or not voltage.v_base_ln_v:
                continue
            rows.append(
                {
                    "bus": node_id,
                    "node": phase_node.get(voltage.phase, 0),
                    "pu": abs(voltage.complex) / voltage.v_base_ln_v,
                    "angle_deg": math.degrees(math.atan2(voltage.complex.imag, voltage.complex.real)),
                }
            )
    return rows


def main() -> None:
    payload = run_pack(Path("reports/external/derived"))
    for item in payload["cases"]:
        print(item["id"], item["verdict"], item["max_voltage_error_pu"], item["max_tap_gap"])
    print("pack", payload["verdict"])


if __name__ == "__main__":
    main()
