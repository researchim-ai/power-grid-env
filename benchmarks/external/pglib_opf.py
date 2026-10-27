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


def load_case(path: Path) -> OpfCase:
    text = path.read_text(encoding="utf-8")
    base = float(re.search(r"mpc\.baseMVA\s*=\s*([0-9.]+)", text).group(1))
    published = re.search(r"opt objective value:\s*([0-9.]+)", text)
    buses = [
        Bus(
            id=int(row[0]),
            type_code=int(row[1]),
            pd=row[2],
            qd=row[3],
            gs=row[4] / base,
            bs=row[5] / base,
            vm=row[7],
            va_deg=row[8],
            vmax=row[11],
            vmin=row[12],
        )
        for row in _matrix(text, "bus")
    ]
    costs = _matrix(text, "gencost")
    generators = []
    for row, cost in zip(_matrix(text, "gen"), costs, strict=True):
        if int(row[7]) != 1:
            continue
        count = int(cost[3])
        generators.append(
            GeneratorSpec(
                bus=int(row[0]),
                qmin=row[4],
                qmax=row[3],
                pmin=row[9],
                pmax=row[8],
                cost=tuple(cost[4 : 4 + count]),
                pg=row[1],
                qg=row[2],
            )
        )
    branches = [
        Branch(
            fbus=int(row[0]),
            tbus=int(row[1]),
            r=row[2],
            x=row[3],
            b=row[4],
            rate_a=row[5],
            ratio=row[8],
            angle_deg=row[9],
            status=int(row[10]),
            angmin_deg=row[11],
            angmax_deg=row[12],
        )
        for row in _matrix(text, "branch")
    ]
    return OpfCase(
        name=path.name,
        base_mva=base,
        buses=buses,
        generators=generators,
        branches=branches,
        published_cost=None if published is None else float(published.group(1)),
    )


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


def _matrix(text: str, name: str) -> list[list[float]]:
    match = re.search(rf"mpc\.{name}\s*=\s*\[(.*?)\];", text, flags=re.S)
    if match is None:
        raise ValueError(f"в кейсе нет mpc.{name}")
    rows = []
    for line in match.group(1).splitlines():
        line = line.split("%")[0].strip().rstrip(";")
        if not line:
            continue
        rows.append([float(item) for item in line.split()])
    return rows


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
