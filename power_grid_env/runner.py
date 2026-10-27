"""Прогон suite: одинаковые сценарии для baseline и кандидата, отдельные RNG.

Дополнительный вызов контроллера не двигает сценарий: погода и отказы живут
в RNG среды. Провал контроллера, провал расчёта и физическое нарушение
различаются.
"""

from __future__ import annotations

import ast
import hashlib
import json
import platform
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
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


METRICS_IDENTITY = {"schema": "power-grid-env.metrics/v1", "voltage_service_pu": 0.95}


def experiment_fingerprint(scenario: Scenario, command: dict[str, Any], network: Network) -> str:
    from power_grid_env.results import SolveOptions

    payload = json.dumps(
        {
            "scenario": scenario.model_dump(mode="json"),
            "command": command,
            "controller_files": _controller_files(command),
            "network": canonical_hash(network),
            "solver": SolveOptions().model_dump(mode="json"),
            "thresholds": METRICS_IDENTITY,
            "runtime": _runtime_identity(),
            "engine": _engine_identity(),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _controller_files(command: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for token in command.get("command") or []:
        path = Path(str(token))
        if path.suffix == ".py" and path.is_file():
            _hash_local_module(path.resolve(), found)
    policy = command.get("policy")
    if policy in {"heuristic", "aggressive"}:
        path = Path(__file__).resolve().parent / "heuristic.py"
        _hash_local_module(path, found)
    if policy == "hold":
        found["policy:hold"] = "hold"
    return found


def _hash_local_module(path: Path, found: dict[str, str]) -> None:
    """Хеш файла и локальных import рядом с ним. Стандартную библиотеку не обходит."""
    path = path.resolve()
    key = str(path)
    if key in found or not path.is_file():
        return
    data = path.read_bytes()
    found[key] = hashlib.sha256(data).hexdigest()
    try:
        tree = ast.parse(data.decode("utf-8"))
    except (SyntaxError, UnicodeError):
        return
    root = path.parent
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    for name in modules:
        candidate = root / f"{name.split('.')[0]}.py"
        if candidate.is_file():
            _hash_local_module(candidate, found)


def _engine_identity() -> str:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _runtime_identity() -> dict[str, Any]:
    packages = {}
    for name in ("numpy", "scipy"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "missing"
    return {"python": sys.version.split()[0], "system": platform.system(), "packages": packages}


def _same_fingerprint(directory: Path, fingerprint: str) -> bool:
    previous = _read_previous(directory)
    return bool(previous and previous.get("fingerprint") == fingerprint)


def load_suite(path: Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def run_suite(
    suite_path: Path,
    out_dir: Path,
    *,
    use_process_backend: bool = True,
    resume: bool = False,
    only: list[str] | None = None,
    idempotency_key: str | None = None,
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
                fingerprint = experiment_fingerprint(scenario, command, network)
                role_dir = out_dir / scenario.id / role
                key = None if idempotency_key is None else f"{idempotency_key}:{scenario.id}:{role}"
                reused = _reuse_attempt(index, role_dir, fingerprint, key, resume=resume, suite_id=suite["id"], scenario_id=scenario.id, role=role)
                if reused is not None:
                    roles[role] = reused
                    continue
                attempt = str(uuid.uuid4())
                attempt_dir = role_dir / "attempts" / attempt
                _start(index, attempt, suite["id"], scenario.id, role, attempt_dir, fingerprint, key)
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
                    roles[role] = {
                        "trace": trace,
                        "metrics": metrics,
                        "outcome": outcome,
                        "fingerprint": fingerprint,
                        "attempt_id": attempt,
                    }
                    _write_attempt(attempt_dir, roles[role])
                    _point_latest(role_dir, attempt, fingerprint)
                    _finish(index, attempt, metrics["episode_outcome"])
                except Exception as exc:
                    failures.append(f"{scenario.id}/{role}: {exc}")
                    roles[role] = None
                    unassessed.append(f"{scenario.id}/{role}")
                    write_json(attempt_dir / "error.json", {"error": str(exc)})
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
        "verdict": _verdict(comparisons, failures, unassessed),
        "candidate_status": "rejected" if any(
            item.get("candidate_worse_on") or item.get("candidate_has_violation_baseline_does_not") for item in comparisons
        ) or bool(failures or unassessed) else "accepted",
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
            if not payload or not payload.get("attempt_id"):
                continue
            # Попытка уже записана в свой каталог. Повторный прогон её не затирает.
        if pair and roles.get("candidate"):
            steps = roles["candidate"]["trace"]
            if network is not None and scenario is not None and steps:
                write_viewer(
                    directory / "viewer.html",
                    viewer_bundle(network.model_dump(mode="json"), steps, scenario.v_min_pu, scenario.v_max_pu),
                )
    if pair:
        write_json(directory / "comparison.json", pair)


def _verdict(comparisons: list[dict[str, Any]], failures: list[str], unassessed: list[str] | None = None) -> str:
    unassessed = list(unassessed or [])
    if not comparisons and not failures and not unassessed:
        return "incomplete"
    if unassessed and not comparisons:
        return "incomplete"
    if any(item.get("candidate_worse_on") or item.get("candidate_has_violation_baseline_does_not") for item in comparisons):
        return "completed_with_recorded_regressions" if not failures and not unassessed else "completed_with_failures"
    if failures or unassessed:
        return "completed_with_failures"
    if not comparisons:
        return "incomplete"
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
        directory text,
        fingerprint text,
        idempotency_key text
        )"""
    )
    columns = {row[1] for row in connection.execute("pragma table_info(attempts)")}
    if "fingerprint" not in columns:
        connection.execute("alter table attempts add column fingerprint text")
    if "idempotency_key" not in columns:
        connection.execute("alter table attempts add column idempotency_key text")
    connection.commit()
    return connection


def _completed(connection: sqlite3.Connection, suite_id: str, scenario_id: str, role: str) -> bool:
    row = connection.execute(
        "select status from attempts where suite_id=? and scenario_id=? and role=? order by started desc limit 1",
        (suite_id, scenario_id, role),
    ).fetchone()
    return bool(row and row[0] in {"completed", "timeout", "controller_crash", "invalid_action", "simulation_failure"})


def _start(connection, attempt, suite_id, scenario_id, role, directory, fingerprint: str, idempotency_key: str | None) -> None:
    connection.execute(
        """insert into attempts
        (attempt_id, suite_id, scenario_id, role, status, started, finished, directory, fingerprint, idempotency_key)
        values (?,?,?,?,?,?,?,?,?,?)""",
        (attempt, suite_id, scenario_id, role, "running", _now(), None, str(directory), fingerprint, idempotency_key),
    )
    connection.commit()


def _reuse_attempt(connection, role_dir: Path, fingerprint: str, key: str | None, *, resume: bool, suite_id: str, scenario_id: str, role: str):
    if key:
        row = connection.execute(
            "select directory, fingerprint, status from attempts where idempotency_key=? order by started desc limit 1",
            (key,),
        ).fetchone()
        if row is not None:
            if row[1] != fingerprint:
                raise ValueError("ключ идемпотентности уже связан с другим отпечатком")
            loaded = _read_attempt(Path(row[0]))
            if row[2] not in {"running", "runner_exception"} and loaded is not None:
                return loaded
            raise ValueError("ключ идемпотентности уже занят, повторный прогон не создаётся")
    if resume and _same_fingerprint(role_dir, fingerprint) and _completed(connection, suite_id, scenario_id, role):
        return _read_previous(role_dir)
    return None


def _write_attempt(directory: Path, payload: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "trace.json", payload["trace"])
    write_json(directory / "metrics.json", payload["metrics"])
    write_json(directory / "fingerprint.json", {"fingerprint": payload["fingerprint"]})


def _point_latest(role_dir: Path, attempt: str, fingerprint: str) -> None:
    role_dir.mkdir(parents=True, exist_ok=True)
    write_json(role_dir / "latest.json", {"attempt_id": attempt, "fingerprint": fingerprint})


def _finish(connection, attempt, status) -> None:
    connection.execute("update attempts set status=?, finished=? where attempt_id=?", (status, _now(), attempt))
    connection.commit()


def _read_attempt(directory: Path) -> dict[str, Any] | None:
    try:
        fingerprint = json.loads((directory / "fingerprint.json").read_text(encoding="utf-8"))["fingerprint"]
        return {
            "metrics": json.loads((directory / "metrics.json").read_text(encoding="utf-8")),
            "trace": json.loads((directory / "trace.json").read_text(encoding="utf-8")),
            "outcome": "resumed",
            "fingerprint": fingerprint,
            "attempt_id": directory.name,
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def _read_previous(directory: Path) -> dict[str, Any] | None:
    latest = directory / "latest.json"
    if not latest.exists():
        return None
    try:
        pointer = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    loaded = _read_attempt(directory / "attempts" / pointer.get("attempt_id", ""))
    if loaded is None or loaded.get("fingerprint") != pointer.get("fingerprint"):
        return None
    return loaded


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
