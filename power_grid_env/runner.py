"""Прогон suite: одинаковые сценарии для baseline и кандидата, отдельные RNG.

Дополнительный вызов контроллера не двигает сценарий: погода и отказы живут
в RNG среды. Провал контроллера, провал расчёта и физическое нарушение
различаются.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from power_grid_env.backend_process import ProcessBackend
from power_grid_env.controllers import ACTION_SCHEMA, OBSERVATION_SCHEMA, InProcessController, SubprocessController
from power_grid_env.evaluator import evaluate_trace
from power_grid_env.importer import import_network
from power_grid_env.metrics import compare_pair
from power_grid_env.model import Network, canonical_hash
from power_grid_env.preflight import preflight
from power_grid_env.report import write_html_report, write_json, write_manifest
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine, agent_model
from power_grid_env.viewer import viewer_bundle, write_viewer


def load_suite(path: Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def run_suite(
    suite_path: Path,
    out_dir: Path,
    *,
    use_process_backend: bool = True,
    resume: bool = False,
    only: list[str] | None = None,
) -> dict[str, Any]:
    suite = load_suite(suite_path)
    if only:
        suite = dict(suite)
        suite["scenarios"] = [item for item in suite["scenarios"] if item["id"] in only]
    out_dir.mkdir(parents=True, exist_ok=True)
    index = _index(out_dir / "index.sqlite")
    backend = ProcessBackend() if use_process_backend else None
    solve = backend.solve if backend is not None else None
    comparisons = []
    failures: list[str] = []
    unassessed: list[str] = []
    assumptions: list[str] = []
    try:
        for scenario_spec in suite["scenarios"]:
            if (out_dir / "CANCEL").exists():
                unassessed.append(f"{scenario_spec['id']}: отменено")
                break
            scenario = Scenario.model_validate(scenario_spec["scenario"])
            model_dir = Path(scenario_spec["model"])
            network, import_report = _load_model(model_dir)
            assumptions.extend(import_report.get("assumptions", []))
            assumptions.extend(import_report.get("approximations", []))
            if network is None:
                failures.append(f"{scenario.id}: импорт не удался")
                unassessed.append(scenario.id)
                _save_case(out_dir / scenario.id, import_report, None, None, None, "import_failed")
                continue
            report = preflight(network, task=suite.get("task", "restoration"))
            if not report["ok"]:
                failures.append(f"{scenario.id}: preflight: {report['errors']}")
                unassessed.append(scenario.id)
                _save_case(out_dir / scenario.id, import_report, report, None, None, "preflight_failed")
                continue
            public_network = agent_model(network, scenario).model_dump(mode="json")
            roles = {}
            for role, command in (("baseline", scenario_spec["baseline"]), ("candidate", scenario_spec["candidate"])):
                if resume and _completed(index, suite["id"], scenario.id, role):
                    roles[role] = _read_previous(out_dir / scenario.id / role)
                    continue
                attempt = str(uuid.uuid4())
                _start(index, attempt, suite["id"], scenario.id, role, out_dir / scenario.id / role)
                try:
                    trace, outcome = _episode(
                        network,
                        scenario,
                        public_network,
                        command,
                        solve,
                        float(scenario_spec.get("timeout_s", 2.0)),
                    )
                    metrics = evaluate_trace(trace, scenario.horizon_s, outcome)
                    roles[role] = {"trace": trace, "metrics": metrics, "outcome": outcome}
                    _finish(index, attempt, metrics["episode_outcome"])
                except Exception as exc:
                    failures.append(f"{scenario.id}/{role}: {exc}")
                    roles[role] = None
                    unassessed.append(f"{scenario.id}/{role}")
                    _finish(index, attempt, "runner_exception")
            if roles.get("baseline") and roles.get("candidate"):
                pair = compare_pair(roles["baseline"]["metrics"], roles["candidate"]["metrics"])
                pair["scenario_id"] = scenario.id
                pair["baseline"] = roles["baseline"]["metrics"]
                pair["candidate"] = roles["candidate"]["metrics"]
                pair["pair_status"] = "compared"
                comparisons.append(pair)
                _save_case(out_dir / scenario.id, import_report, report, roles, pair, "compared", network, scenario)
            else:
                unassessed.append(scenario.id)
    finally:
        if backend is not None:
            backend.close()
    summary = {
        "title": suite.get("title", suite["id"]),
        "suite_id": suite["id"],
        "comparisons": comparisons,
        "failures": failures,
        "unassessed": unassessed,
        "assumptions": sorted(set(assumptions)),
        "applicability": suite.get(
            "applicability",
            "Квазистационарная несимметричная сеть, известный повреждённый участок, "
            "коммутация. RMS, EMT и быстродействующая релейная защита вне области.",
        ),
        "verdict": _verdict(comparisons, failures),
        "verdict_reason": "Пороги suite фиксированы до прогона. Несходимость решателя не названа коллапсом.",
    }
    write_json(out_dir / "summary.json", summary)
    write_html_report(out_dir / "report.html", summary)
    write_manifest(out_dir)
    return summary


def _episode(network, scenario, public_network, command, solve, timeout_s):
    controller = _controller(command, timeout_s)
    engine = Engine(network.model_copy(deep=True), scenario, solve=solve)
    try:
        public = scenario.public_config(public_network)
        controller.initialize(OBSERVATION_SCHEMA, ACTION_SCHEMA, public)
        controller.reset({"scenario_id": scenario.id, "seed_scope": "scenario-rng-not-shared"})
        observation = engine.reset()
        outcome = "completed"
        stopped = False
        guard = 0
        while not engine.terminated and not engine.truncated and guard < 100:
            guard += 1
            if stopped:
                origin = "timeout_policy" if outcome == "timeout" else "crash_policy"
                engine.step_control({"kind": "hold"}, origin=origin)
                continue
            response = controller.act(observation, _public_history(engine))
            kind = response.get("outcome", "ok")
            if kind == "timeout":
                outcome = "timeout"
                stopped = True
                engine.step_control({"kind": "hold"}, origin="timeout_policy")
            elif kind == "controller_crash":
                outcome = "controller_crash"
                stopped = True
                engine.step_control({"kind": "hold"}, origin="crash_policy")
            elif kind == "invalid_action":
                outcome = "invalid_action"
                engine.step_control({"kind": "not-an-action"}, origin="controller")
            else:
                engine.step_control(response.get("action") or {"kind": "hold"}, origin="controller")
            observation = engine.observation()
            if _hidden_leaked(controller.sent, scenario.hidden_canary):
                outcome = "information_leak"
                break
        if engine.trace and any(step["solve_status"] not in {"converged", "deenergized_no_source", None} for step in engine.trace):
            if outcome == "completed":
                outcome = "simulation_failure"
        return engine.trace, outcome
    finally:
        controller.close()


def _controller(command: dict[str, Any], timeout_s: float):
    if "command" in command:
        return SubprocessController(command["command"], timeout_s=timeout_s)
    if command.get("policy") == "heuristic":
        from power_grid_env.heuristic import choose_action

        return InProcessController(lambda obs, public, history: choose_action(obs, public["network"], respect_rating=True))
    if command.get("policy") == "aggressive":
        from power_grid_env.heuristic import choose_action

        return InProcessController(lambda obs, public, history: choose_action(obs, public["network"], respect_rating=False))
    if command.get("policy") == "hold":
        return InProcessController(lambda obs, public, history: {"kind": "hold", "rationale": "baseline бездействия"})
    raise ValueError(f"контроллер не задан: {command}")


def _public_history(engine: Engine) -> list[dict[str, Any]]:
    return [{"time_s": item["time_s"], "action": item["action"], "reason": item["acceptance"].get("reason")} for item in engine.history]


def _hidden_leaked(sent: list[dict[str, Any]], canary: str | None) -> bool:
    if not canary:
        return False
    blob = json_dumps(sent)
    return canary in blob or "fault_r_ohm" in blob or "hidden_canary" in blob


def json_dumps(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def _load_model(model_dir: Path) -> tuple[Network | None, dict[str, Any]]:
    model_dir = Path(model_dir)
    if model_dir.is_file():
        return import_network(model_dir)
    dss = next(model_dir.glob("*.dss"), None)
    sidecar = next(model_dir.glob("*.yaml"), None)
    if dss is None:
        json_file = next(model_dir.glob("*.json"), None)
        if json_file is None:
            return None, {"errors": [f"в {model_dir} нет модели"], "assumptions": [], "approximations": []}
        return import_network(json_file)
    return import_network(dss, sidecar)


def _save_case(directory, import_report, preflight_report, roles, pair, status, network=None, scenario=None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "import_report.json", import_report)
    write_json(directory / "preflight.json", preflight_report or {})
    write_json(directory / "status.json", {"status": status})
    if network is not None:
        write_json(directory / "network_hash.json", {"sha256": canonical_hash(network)})
    if roles:
        for role, payload in roles.items():
            if not payload:
                continue
            role_dir = directory / role
            role_dir.mkdir(exist_ok=True)
            write_json(role_dir / "trace.json", payload["trace"])
            write_json(role_dir / "metrics.json", payload["metrics"])
        if pair and roles.get("candidate"):
            steps = roles["candidate"]["trace"]
            if network is not None and scenario is not None and steps:
                write_viewer(
                    directory / "viewer.html",
                    viewer_bundle(network.model_dump(mode="json"), steps, scenario.v_min_pu, scenario.v_max_pu),
                )
    if pair:
        write_json(directory / "comparison.json", pair)


def _verdict(comparisons: list[dict[str, Any]], failures: list[str]) -> str:
    if not comparisons and failures:
        return "fail"
    if any(item.get("candidate_worse_on") or item.get("candidate_has_violation_baseline_does_not") for item in comparisons):
        return "completed_with_recorded_regressions"
    if failures:
        return "completed_with_failures"
    return "pass"


def _index(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        """create table if not exists attempts (
        attempt_id text primary key,
        suite_id text,
        scenario_id text,
        role text,
        status text,
        started text,
        finished text,
        directory text
        )"""
    )
    connection.commit()
    return connection


def _completed(connection: sqlite3.Connection, suite_id: str, scenario_id: str, role: str) -> bool:
    row = connection.execute(
        "select status from attempts where suite_id=? and scenario_id=? and role=? order by started desc limit 1",
        (suite_id, scenario_id, role),
    ).fetchone()
    return bool(row and row[0] in {"completed", "timeout", "controller_crash", "invalid_action", "simulation_failure"})


def _start(connection, attempt, suite_id, scenario_id, role, directory) -> None:
    connection.execute(
        "insert into attempts values (?,?,?,?,?,?,?,?)",
        (attempt, suite_id, scenario_id, role, "running", _now(), None, str(directory)),
    )
    connection.commit()


def _finish(connection, attempt, status) -> None:
    connection.execute("update attempts set status=?, finished=? where attempt_id=?", (status, _now(), attempt))
    connection.commit()


def _read_previous(directory: Path) -> dict[str, Any] | None:
    metrics = directory / "metrics.json"
    trace = directory / "trace.json"
    if not metrics.exists() or not trace.exists():
        return None
    import json

    return {
        "metrics": json.loads(metrics.read_text(encoding="utf-8")),
        "trace": json.loads(trace.read_text(encoding="utf-8")),
        "outcome": "resumed",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
