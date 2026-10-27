"""Один установленный сценарий: импорт, проверка, шаг, отчёт, повтор из снимка.

Не ищет корень репозитория. Модель и контроллер задаются путями.
"""

from __future__ import annotations

import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from power_grid_env import __version__
from power_grid_env.controllers import SubprocessController
from power_grid_env.importer import import_network
from power_grid_env.paths import SUPPORTED_SYSTEMS, project_dir
from power_grid_env.preflight import preflight
from power_grid_env.report import write_json
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine


def run_case(
    model: Path,
    out: Path,
    *,
    sidecar: Path | None = None,
    controller: list[str] | None = None,
    task: str = "power_flow",
) -> dict[str, Any]:
    network, imported = import_network(model, sidecar)
    payload: dict[str, Any] = {
        "version": __version__,
        "runtime": _runtime(),
        "supported_systems": list(SUPPORTED_SYSTEMS),
        "project": str(project_dir()),
        "import_ok": network is not None and not imported.get("errors"),
        "import_errors": imported.get("errors") or [],
    }
    if network is None:
        payload["ok"] = False
        write_json(out / "case.json", payload)
        return payload
    checked = preflight(network, task=task, solve_initial=True)
    payload["preflight_ok"] = checked["ok"]
    payload["preflight_errors"] = checked["errors"]
    if not checked["ok"]:
        payload["ok"] = False
        write_json(out / "case.json", payload)
        return payload
    scenario = Scenario(id=network.id, horizon_s=60, control_interval_s=60, settle_s=600)
    engine = Engine(network, scenario)
    engine.reset()
    if controller:
        held = SubprocessController(controller, timeout_s=2.0)
        try:
            held.initialize({}, {}, scenario.public_config({}))
            response = held.act(engine.observation(), [])
        finally:
            held.close()
        action = response.get("action") or {"kind": "hold"}
        if response.get("outcome") != "ok":
            action = {"kind": "hold"}
            payload["controller_outcome"] = response.get("outcome")
        else:
            payload["controller_outcome"] = "ok"
    else:
        action = {"kind": "hold"}
        payload["controller_outcome"] = "not_used"
    step = engine.step_control(action)
    snapshot = engine.snapshot()
    restored = Engine(network, scenario)
    restored.restore(snapshot)
    payload.update(
        {
            "ok": step["solve_status"] in {"converged", "deenergized_no_source"} and restored.time_s == engine.time_s,
            "solve_status": step["solve_status"],
            "time_s": engine.time_s,
            "resumed_time_s": restored.time_s,
            "energy_not_served_wh": step["energy_not_served_wh"],
        }
    )
    write_json(out / "case.json", payload)
    return payload


def _runtime() -> dict[str, str]:
    packages = {}
    for name in ("numpy", "scipy", "pydantic"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "missing"
    return {"python": sys.version.split()[0], "system": platform.system(), "packages": str(packages)}
