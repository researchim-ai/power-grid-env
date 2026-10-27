"""Один фидер SMART-DS: снимок и QSTS первых суток и первой недели.

Окна выбраны до просмотра ошибки: первые 96 и первые 672 интервала по 15 минут.
Это синтетические профили, не полевые измерения. Полный bucket не скачивается.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import shutil
import time
from pathlib import Path
from typing import Any

from benchmarks.external._common import PROCESSED, RAW, environment, sha256_file, write_json
from benchmarks.external.compare import compare_buses
from benchmarks.external.run import _imported_rows

FEEDER = RAW / (
    "smart-ds/v1.0/2018/SFO/P1U/opendss/p1uhs0_1247/p1uhs0_1247--p1udt104"
)
DAY_STEPS = 96
WEEK_STEPS = 7 * DAY_STEPS


def run(out: Path, *, steps: int) -> dict[str, Any]:
    master = FEEDER / "Master.dss"
    if not master.exists():
        payload = {"verdict": "data_unavailable", "reason": "мастер фидера не скачан"}
        write_json(out / "qsts.json", payload)
        return payload
    profiles = _required_profiles(FEEDER / "LoadShapes.dss")
    missing = [name for name in profiles.values() if not name.exists()]
    if missing:
        payload = {
            "verdict": "data_unavailable",
            "reason": "нет CSV профилей этого фидера",
            "missing": [path.name for path in missing[:8]],
        }
        write_json(out / "qsts.json", payload)
        return payload
    work = _work_copy(FEEDER)
    from power_grid_env.importer import import_network
    from power_grid_env.backend_opendss import solve_network

    network, report = import_network(work / "Master.dss")
    if network is None:
        payload = {"verdict": "unsupported", "errors": report.get("errors")}
        write_json(out / "qsts.json", payload)
        return payload
    base_p = {load.id: dict(load.p_w) for load in network.loads}
    base_q = {load.id: dict(load.q_var) for load in network.loads}
    shapes = _shape_table(work / "LoadShapes.dss", work)
    load_shape = _load_shapes(work / "Loads.dss")
    native = _native_episode(work / "Master.dss", steps)
    profile_sha = {path.name: sha256_file(path) for path in profiles.values()}
    ours_errors = []
    ours_times = []
    ours_rows = []
    checkpoint = None
    midpoint = steps // 2
    for step in range(steps):
        _apply(network, base_p, base_q, load_shape, shapes, step)
        started = time.perf_counter()
        solved = solve_network(network)
        ours_times.append(time.perf_counter() - started)
        rows = _imported_rows(network, solved)
        ours_rows.append(rows)
        comparison = compare_buses(native["frames"][step], rows)
        ours_errors.append(comparison["max_voltage_error_pu"])
        if step + 1 == midpoint:
            checkpoint = _checkpoint(network, midpoint, base_p, base_q, profile_sha)
    replay = _replay(checkpoint, load_shape, shapes, ours_rows, steps, profile_sha)
    peak = max(ours_errors) if ours_errors else None
    verdict = "pass" if peak is not None and peak <= 1e-5 and native["converged"] and replay["verdict"] == "pass" else "fail"
    if checkpoint is not None:
        write_json(out / f"checkpoint_{steps}.json", checkpoint)
    payload = {
        "feeder": "p1uhs0_1247--p1udt104",
        "evidence": ["synthetic_scale", "conversion_consistency"],
        "selection_rule": "самый маленький Master.dss в opendss_no_loadshapes, при равенстве по пути, до решения",
        "window": {
            "start_index": 0,
            "steps": steps,
            "step_minutes": 15,
            "reason": "первые интервалы года, сутки=96 и неделя=672, зафиксировано до оценки ошибки",
        },
        "verdict": verdict,
        "max_voltage_error_pu": peak,
        "native_seconds": native["seconds"],
        "native_step_seconds": _timing(native["step_seconds"]),
        "import_step_seconds": _timing(ours_times),
        "rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "environment": environment(),
        "profile_sha256": profile_sha,
        "replay": replay,
    }
    write_json(out / f"qsts_{steps}.json", payload)
    return payload


def _required_profiles(path: Path) -> dict[str, Path]:
    found = {}
    for name in set(re.findall(r"file=([^)\s]+)", path.read_text(encoding="utf-8"))):
        found[name] = (path.parent / name).resolve()
    return found


def _work_copy(source: Path) -> Path:
    work = PROCESSED / "smart-ds" / source.name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for item in source.iterdir():
        if not item.is_file():
            continue
        if item.name == "LoadShapes.dss":
            text = item.read_text(encoding="utf-8")

            def absolute(match: re.Match[str]) -> str:
                return "file=" + str((source / match.group(1)).resolve())

            (work / item.name).write_text(re.sub(r"file=([^)\s]+)", absolute, text), encoding="utf-8")
        elif item.name == "Master.dss":
            kept = []
            for line in item.read_text(encoding="utf-8").splitlines():
                if line.strip().lower().startswith(("solve", "export", "plot")):
                    kept.append("! derived: годовой Solve снят, окно задаёт прогон")
                    continue
                kept.append(line)
            (work / item.name).write_text("\n".join(kept) + "\n", encoding="utf-8")
        else:
            (work / item.name).symlink_to(item.resolve())
    return work


def _shape_table(path: Path, work: Path) -> dict[str, tuple[list[float], list[float]]]:
    table = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.lower().startswith("new loadshape"):
            continue
        name = raw.split()[1].split(".", 1)[1]
        files = re.findall(r"file=([^)\s]+)", raw)
        active = _read_column(work / files[0] if not Path(files[0]).is_absolute() else Path(files[0]))
        reactive = _read_column(work / files[1] if not Path(files[1]).is_absolute() else Path(files[1]))
        table[name.lower()] = (active, reactive)
    return table


def _read_column(path: Path) -> list[float]:
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        token = line.strip().split(",")[0].strip()
        if not token:
            continue
        try:
            values.append(float(token))
        except ValueError:
            continue
    return values


def _load_shapes(path: Path) -> dict[str, str]:
    found = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "yearly=" not in raw.lower():
            continue
        name = raw.split()[1].split(".", 1)[1]
        match = re.search(r"(?i)yearly=(\S+)", raw)
        if match:
            found[name] = match.group(1).lower()
    return found


def _checkpoint(network: Any, cursor: int, base_p: dict, base_q: dict, profile_sha: dict[str, str]) -> dict[str, Any]:
    """Снимок посередине окна. Курсор — следующий шаг, профили входят в отпечаток."""
    payload = {
        "cursor": cursor,
        "step_minutes": 15,
        "network": network.model_dump(mode="json"),
        "base_p": base_p,
        "base_q": base_q,
        "profile_sha256": profile_sha,
    }
    payload["sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return payload


def _replay(
    checkpoint: dict[str, Any] | None,
    load_shape: dict[str, str],
    shapes: dict,
    continuous: list[list[dict[str, Any]]],
    steps: int,
    profile_sha: dict[str, str],
) -> dict[str, Any]:
    if checkpoint is None:
        return {"verdict": "not_run", "reason": "для разреза нужны хотя бы два шага"}
    if checkpoint["profile_sha256"] != profile_sha:
        return {"verdict": "fail", "reason": "отпечаток профилей не совпал, продолжение отклонено"}
    from power_grid_env.model import Network
    from power_grid_env.backend_opendss import solve_network

    restored = Network.model_validate(checkpoint["network"])
    errors = []
    for step in range(int(checkpoint["cursor"]), steps):
        _apply(restored, checkpoint["base_p"], checkpoint["base_q"], load_shape, shapes, step)
        solved = solve_network(restored)
        if not solved.converged:
            return {
                "verdict": "fail",
                "cursor": checkpoint["cursor"],
                "checkpoint_sha256": checkpoint["sha256"],
                "reason": f"шаг {step} после восстановления не сошёлся",
                "gate_voltage_pu": 1e-5,
            }
        comparison = compare_buses(_as_native(continuous[step]), _imported_rows(restored, solved))
        errors.append(comparison["max_voltage_error_pu"])
    peak = max(errors) if errors else None
    verdict = "pass" if peak is not None and peak <= 1e-5 else "fail"
    return {
        "verdict": verdict,
        "cursor": checkpoint["cursor"],
        "checkpoint_sha256": checkpoint["sha256"],
        "max_voltage_error_pu": peak,
        "compared_steps": len(errors),
        "gate_voltage_pu": 1e-5,
    }


def _as_native(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buses: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buses.setdefault(row["bus"], []).append(
            {"node": row["node"], "pu": row["pu"], "angle_deg": row["angle_deg"]}
        )
    return {"converged": True, "buses": [{"name": name, "phases": phases} for name, phases in buses.items()]}


def _apply(network: Any, base_p: dict, base_q: dict, load_shape: dict[str, str], shapes: dict, step: int) -> None:
    for load in network.loads:
        shape = shapes.get(load_shape.get(load.id, ""), None)
        if shape is None:
            continue
        active, reactive = shape
        for phase, value in base_p[load.id].items():
            load.p_w[phase] = value * active[step]
        for phase, value in base_q[load.id].items():
            load.q_var[phase] = value * reactive[step]


def _native_episode(master: Path, steps: int) -> dict[str, Any]:
    from opendssdirect.OpenDSSDirect import OpenDSSDirect

    previous = Path.cwd()
    session = OpenDSSDirect().NewContext()
    started = time.perf_counter()
    session.Text.Command("clear")
    session.Text.Command(f'compile "{master.resolve()}"')
    session.Text.Command("Set mode=yearly stepsize=15m number=1")
    session.Text.Command("Set tolerance=1e-10")
    session.Text.Command("Set maxiterations=200")
    compile_seconds = time.perf_counter() - started
    frames = []
    step_seconds = []
    for _ in range(steps):
        tick = time.perf_counter()
        session.Solution.Solve()
        step_seconds.append(time.perf_counter() - tick)
        frames.append(_frame(session))
    os.chdir(previous)
    return {
        "seconds": compile_seconds,
        "step_seconds": step_seconds,
        "frames": frames,
        "converged": all(frame["converged"] for frame in frames),
    }


def _frame(session: Any) -> dict[str, Any]:
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
    return {"converged": bool(session.Solution.Converged()), "buses": buses}


def _timing(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {}
    warm = samples[1:] or samples
    ordered = sorted(warm)

    def pct(rank: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round(rank * (len(ordered) - 1)))))
        return ordered[index]

    return {"cold_s": samples[0], "warm_p50_s": pct(0.50), "warm_p95_s": pct(0.95), "n": float(len(samples))}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="benchmarks.external.smart_ds_qsts")
    parser.add_argument("--steps", type=int, default=DAY_STEPS)
    parser.add_argument("--out", type=Path, default=Path("reports/external/smart-ds"))
    args = parser.parse_args()
    payload = run(args.out, steps=args.steps)
    print({"verdict": payload["verdict"], "steps": args.steps, "max_v": payload.get("max_voltage_error_pu")})


if __name__ == "__main__":
    main()
