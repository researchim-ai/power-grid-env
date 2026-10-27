"""Прогон пакета: прямой эталон, штатный импорт, вердикт без подгонки допусков."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import yaml

from benchmarks.external._common import PROCESSED, RAW, environment, write_json
from benchmarks.external.compare import compare_buses
from benchmarks.external.extract_native_reference import extract_master
from benchmarks.external.fetch import fetch_source

ROOT = Path(__file__).resolve().parents[2]


def run_pack(pack_path: Path, out: Path, *, offline: bool = False) -> dict[str, Any]:
    pack = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "environment.json", environment())
    if pack.get("verdict") == "not_assessed_missing_paired_measurements":
        from benchmarks.external.field_gap import assessment

        gap = assessment()
        payload = {
            "pack": pack["id"],
            "verdict": "not_assessed_missing_paired_measurements",
            "required": pack.get("required") or [],
            "cases": [],
            "score_computed": False,
            "request_sent": False,
            "thresholds_frozen_not_applied": gap["thresholds_frozen_not_applied"],
        }
        write_json(out / "results.json", payload)
        _html(out, payload)
        return payload
    cases = []
    for case in pack.get("cases") or []:
        if case.get("status") == "not_run":
            cases.append({"id": case["id"], "verdict": "not_run", "source": case.get("source")})
            continue
        cases.append(_run_case(case, out, offline=offline))
    required = [item for item in cases if item["verdict"] not in {"not_run"}]
    if not required:
        summary = "not_run"
    elif any(item["verdict"] == "fail" for item in required):
        summary = "fail"
    elif any(item["verdict"] in {"unsupported", "data_unavailable", "inconclusive"} for item in required):
        summary = "inconclusive"
    elif all(item["verdict"] == "pass" for item in required):
        summary = "pass"
    else:
        summary = "inconclusive"
    payload = {"pack": pack["id"], "verdict": summary, "cases": cases}
    write_json(out / "results.json", payload)
    write_json(out / "coverage.json", {"cases": [{"id": item["id"], "coverage": item.get("coverage")} for item in cases]})
    _html(out, payload)
    return payload


def _run_case(case: dict[str, Any], out: Path, *, offline: bool) -> dict[str, Any]:
    source = case["source"]
    fetched = fetch_source(source, offline=offline)
    case_dir = out / "cases" / case["id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    if fetched["status"] != "downloaded":
        result = {"id": case["id"], "source": source, "verdict": fetched["status"], "fetch": fetched}
        write_json(case_dir / "status.json", result)
        return result
    master = _master_path(case)
    if master is None or not master.exists():
        result = {"id": case["id"], "source": source, "verdict": "data_unavailable", "reason": "master не найден"}
        write_json(case_dir / "status.json", result)
        return result
    native = extract_master(master, case_dir / "reference_raw.json", evidence=case.get("evidence", "conversion_consistency"))
    imported, report = _import_case(master)
    coverage = _coverage(report)
    write_json(case_dir / "import_report.json", report)
    write_json(case_dir / "feature_coverage.json", coverage)
    if imported is None or not native.get("converged"):
        verdict = "data_unavailable" if native.get("error") and imported is None else "unsupported" if imported is None else "fail"
        if native.get("error") and "could not be opened" in native.get("error", ""):
            verdict = "data_unavailable"
        result = {
            "id": case["id"],
            "source": source,
            "verdict": verdict,
            "native_converged": native.get("converged"),
            "native_error": native.get("error"),
            "import_errors": report.get("errors"),
            "coverage": coverage,
            "evidence": case.get("evidence"),
        }
        write_json(case_dir / "status.json", result)
        return result
    from power_grid_env.backend_opendss import solve_network

    solved = solve_network(imported)
    rows = _imported_rows(imported, solved)
    comparison = compare_buses(native, rows)
    write_json(case_dir / "comparison.json", comparison)
    result = {
        "id": case["id"],
        "source": source,
        "verdict": comparison["verdict"],
        "native_converged": True,
        "import_status": solved.status,
        "max_voltage_error_pu": comparison["max_voltage_error_pu"],
        "coverage": coverage,
        "evidence": "conversion_consistency",
    }
    write_json(case_dir / "status.json", result)
    return result


def _master_path(case: dict[str, Any]) -> Path | None:
    from benchmarks.external._common import RAW

    if case["source"] == "ieee-pes":
        return RAW / "ieee-pes" / case.get("folder", "13Bus") / case["master"]
    if case["source"] == "enwl-lvns":
        return _extract_enwl_member(case["archive_member"], case["master"])
    return None


def _extract_enwl_member(member: str, master_name: str) -> Path | None:
    from benchmarks.external._common import RAW
    from benchmarks.external.fetch import assert_zip_safe

    archive = RAW / "enwl-lvns" / "lv-network-models-2.zip"
    if not archive.exists():
        return None
    assert_zip_safe(archive)
    destination = PROCESSED / "enwl-lvns" / Path(member).name
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for name in zipped.namelist():
            if not name.startswith(member) or name.endswith("/"):
                continue
            target = destination / Path(name).name
            target.write_bytes(zipped.read(name))
    return destination / master_name


def _import_case(master: Path) -> tuple[Any, dict[str, Any]]:
    from power_grid_env.importer import import_network

    sidecar = master.with_suffix(".yaml")
    if master.suffix.lower() == ".txt":
        sidecar = master.with_name("pge-import.yaml")
    if sidecar.exists():
        return import_network(master, sidecar)
    return import_network(master)


def _coverage(report: dict[str, Any]) -> dict[str, Any]:
    commands = report.get("commands") or []
    counts: dict[str, int] = {}
    unsupported = []
    for row in commands:
        counts[row.get("status", "unknown")] = counts.get(row.get("status", "unknown"), 0) + 1
        if row.get("status") == "unsupported":
            unsupported.append(row.get("command"))
    return {"counts": counts, "unsupported": unsupported[:80], "errors": report.get("errors") or []}


def _imported_rows(network: Any, solved: Any) -> list[dict[str, Any]]:
    from power_grid_env.topology import compile_topology

    topology = compile_topology(network)
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
                    "angle_deg": __import__("math").degrees(__import__("math").atan2(voltage.complex.imag, voltage.complex.real)),
                }
            )
    return rows


def _html(out: Path, payload: dict[str, Any]) -> None:
    rows = []
    for case in payload["cases"]:
        rows.append(
            "<tr><td>{id}</td><td>{verdict}</td><td>{detail}</td></tr>".format(
                id=case.get("id"),
                verdict=case.get("verdict"),
                detail=case.get("native_error") or case.get("import_errors") or case.get("max_voltage_error_pu") or "",
            )
        )
    text = f"""<!DOCTYPE html><html lang="ru"><meta charset="utf-8"><title>Внешний бенчмарк</title>
<body><h1>{payload['pack']}: {payload['verdict']}</h1>
<p>conversion_consistency — тот же OpenDSS, не полевое измерение и не независимый решатель.</p>
<table border="1" cellpadding="4"><tr><th>Случай</th><th>Вердикт</th><th>Деталь</th></tr>{''.join(rows)}</table>
</body></html>"""
    (out / "report.html").write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="benchmarks.external.run")
    parser.add_argument("--pack", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    payload = run_pack(Path(args.pack), Path(args.out), offline=args.offline)
    print(json.dumps({"verdict": payload["verdict"], "pack": payload["pack"]}, ensure_ascii=False))
    return 0 if payload["verdict"] in {"pass", "inconclusive", "not_assessed_missing_paired_measurements"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
