"""Приёмка первой поставки по разделам 16–20 технического задания."""

from __future__ import annotations

import json
import os
from pathlib import Path

from power_grid_env.dataset import export_traces
from power_grid_env.gym_env import RestorationEnv
from power_grid_env.importer import import_network
from power_grid_env.model import canonical_hash
from power_grid_env.numerical_suite import run_numerical_suite
from power_grid_env.paths import repo_root
from power_grid_env.report import sha256_file
from power_grid_env.runner import run_suite
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine, replay_trace


def run_acceptance(root: Path | None = None) -> int:
    root = root or repo_root()
    os.chdir(root)
    problems: list[str] = []
    out = root / "reports" / "acceptance"
    network, report = import_network(
        root / "cases/acceptance/feeder_north/feeder.dss",
        root / "cases/acceptance/feeder_north/pge-import.yaml",
    )
    if network is None or report["errors"]:
        problems.append(f"импорт фидера: {report['errors']}")
    before = (root / "cases/acceptance/bad_feeder/feeder.dss").read_bytes()
    rejected, rejected_report = import_network(
        root / "cases/acceptance/bad_feeder/feeder.dss",
        root / "cases/acceptance/bad_feeder/pge-import.yaml",
    )
    if rejected is not None or not rejected_report["errors"]:
        problems.append("несовместимый фидер не отклонён")
    if (root / "cases/acceptance/bad_feeder/feeder.dss").read_bytes() != before:
        problems.append("импорт изменил исходный файл")
    summary = run_suite(root / "cases/suites/restoration_v1.yaml", out, use_process_backend=True)
    worse = [item["scenario_id"] for item in summary["comparisons"] if item.get("candidate_worse_on") or item.get("candidate_has_violation_baseline_does_not")]
    if not worse:
        problems.append("нет сценария, где кандидат хуже baseline или нарушает ограничение")
    outcomes = {item["scenario_id"]: item["candidate"].get("controller_outcome") for item in summary["comparisons"]}
    for required in ("controller_timeout", "controller_crash", "invalid_action"):
        if outcomes.get(required) != required.replace("controller_", "").replace("invalid_action", "invalid_action"):
            # timeout scenario id maps to outcome timeout
            expected = {"controller_timeout": "timeout", "controller_crash": "controller_crash", "invalid_action": "invalid_action"}[required]
            if outcomes.get(required) != expected:
                problems.append(f"{required}: получен исход {outcomes.get(required)}")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        digest = sha256_file(out / item["path"])
        if digest != item["sha256"]:
            problems.append(f"контрольная сумма {item['path']}")
    if not (out / "report.html").exists():
        problems.append("нет HTML-отчёта")
    viewer = out / "normal_fault" / "viewer.html"
    if not viewer.exists() or "привилегированный вид" not in viewer.read_text(encoding="utf-8"):
        problems.append("нет инженерного просмотра")
    if network is not None:
        scenario = Scenario.model_validate(
            {
                "id": "replay",
                "seed": 11,
                "horizon_s": 180,
                "control_interval_s": 60,
                "settle_s": 60,
                "fault_line_id": "damaged",
                "fault_kind": "3ph",
                "fault_phases": ["A", "B", "C"],
                "fault_r_ohm": 1.0,
                "hidden_canary": "CANARY-replay",
            }
        )
        engine = Engine(network, scenario)
        engine.reset()
        from power_grid_env.heuristic import choose_action
        from power_grid_env.simulation import agent_model

        public = scenario.public_config(agent_model(network, scenario).model_dump(mode="json"))
        guard = 0
        while not engine.terminated and not engine.truncated and guard < 6:
            engine.step_control(choose_action(engine.observation(), public["network"], respect_rating=True))
            guard += 1
        snap = engine.snapshot()
        restored = Engine(network, scenario)
        restored.restore(snap)
        if abs(restored.time_s - engine.time_s) > 1e-9:
            problems.append("checkpoint не восстановил время")
        replayed = replay_trace(network, scenario, engine.trace)
        if len(replayed.trace) != len(engine.trace):
            problems.append("replay изменил число шагов")
        else:
            for left, right in zip(engine.trace, replayed.trace):
                if abs(left["energy_not_served_wh"] - right["energy_not_served_wh"]) > 1e-6:
                    problems.append("replay разошёлся по недоотпуску")
                    break
        export_traces([engine.trace], out / "dataset.parquet", scenario_id="replay", model_hash=canonical_hash(network))
        env = RestorationEnv(network, scenario)
        observation, _info = env.reset(seed=11)
        _observation, _reward, _terminated, _truncated, info = env.step(0)
        if "solve_status" not in info:
            problems.append("Gymnasium rollout не вернул статус расчёта")
        if observation["voltage_pu"].shape != (len(env.channels),):
            problems.append("форма наблюдения Gymnasium")
        if observation["voltage_valid"].shape != observation["voltage_pu"].shape:
            problems.append("маска достоверности напряжения не совпала с каналами")
    checks = run_numerical_suite()
    if not all(item["passed"] for item in checks):
        problems.append("численная приёмка: " + ", ".join(item["id"] for item in checks if not item["passed"]))
    (out / "acceptance.json").write_text(
        json.dumps({"ok": not problems, "problems": problems, "summary_verdict": summary["verdict"], "worse": worse}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1
