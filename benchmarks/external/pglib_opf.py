"""Малые кейсы PGLib-OPF. Локальное решение сверяется по допустимости, не по глобальности."""

from __future__ import annotations

import re
from pathlib import Path

from benchmarks.external._common import RAW, write_json
from power_grid_env.network_opf import Branch, Bus, GeneratorSpec, OpfCase, solve_ac_opf

CASES = (
    "pglib_opf_case3_lmbd.m",
    "pglib_opf_case5_pjm.m",
    "pglib_opf_case14_ieee.m",
    "pglib_opf_case118_ieee.m",
)


from power_grid_env.matpower_case import load_case


def evaluate(path: Path) -> dict:
    case = load_case(path)
    result = solve_ac_opf(case)
    published = case.published_cost
    gap = None
    if result["cost"] is not None and published:
        gap = (result["cost"] - published) / published
    # Локальный разрыв с опубликованным числом не превращается в «глобальный оптимум».
    verdict = "pass" if result["success"] else "fail"
    return {
        "case": case.name,
        "verdict": verdict,
        "cost": None if result["cost"] is None else float(result["cost"]),
        "published_cost": published,
        "relative_gap_to_published": None if gap is None else float(gap),
        "global_optimum_claimed": False,
        "max_mismatch_pu": float(result["audit"]["max_mismatch_pu"]),
        "flows_within_rating": bool(result["audit"]["flows_within_rating"]),
    }



def write_report(out: Path) -> dict:
    root = RAW / "pglib-opf"
    cases = [evaluate(root / name) for name in CASES if (root / name).exists()]
    payload = {
        "pack": "pglib-opf",
        "verdict": "pass" if cases and all(item["verdict"] == "pass" for item in cases) else "fail",
        "global_optimum_claimed": False,
        "note": "pass означает независимую допустимость локального решения. Совпадение с опубликованной стоимостью не доказывает глобальный оптимум.",
        "cases": cases,
    }
    write_json(out / "results.json", payload)
    return payload


def main() -> None:
    payload = write_report(Path("reports/external/pglib"))
    for item in payload["cases"]:
        print(item)


if __name__ == "__main__":
    main()
