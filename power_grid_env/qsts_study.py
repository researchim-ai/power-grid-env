"""Durable hold-policy QSTS studies with streamed engineering results.

A checkpoint commits a prefix of the journal. Resume verifies that prefix and
rolls back uncommitted rows. The output is privileged engineering evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable

from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json
from power_grid_env.scenarios import Scenario
from power_grid_env.simulation import Engine


def _encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def run_qsts(spec, out: Path, *, resume: bool = False, max_steps: int | None = None,
             checkpoint_every: int = 96, cancelled: Callable[[], bool] | None = None,
             solver=None) -> dict[str, Any]:
    """Run complete intervals; max_steps pauses, cancellation preserves progress.

    Only one writer is allowed per run. A stale .lock requires the operator to
    verify the old process has stopped before removing it. No automatic unsafe
    lock stealing is attempted.
    """
    if checkpoint_every < 1 or (max_steps is not None and max_steps < 1):
        raise ValueError("step/checkpoint limits must be positive")
    if spec.scenario is None:
        raise ValueError("qsts requires a scenario")
    scenario = Scenario.model_validate(spec.scenario.model_dump())
    # A study runs its full horizon rather than stopping once restoration settles.
    scenario.settle_s = scenario.horizon_s + scenario.control_interval_s
    scenario.trace_limit = min(scenario.trace_limit or 2, 2)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, str(os.getpid()).encode())
        return _run(spec, scenario, out, resume, max_steps, checkpoint_every, cancelled, solver)
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def _run(spec, scenario, out, resume, max_steps, checkpoint_every, cancelled, solver):
    identity = hashlib.sha256(_encoded(spec.model_dump(mode="json"))).hexdigest()
    code = package_fingerprint()
    checkpoint = out / "checkpoint.json"
    journal = out / "intervals.jsonl"
    engine = Engine(spec.network, scenario, solve=solver)
    count = 0
    digest = hashlib.sha256()
    committed_bytes = 0
    if resume:
        saved = json.loads(checkpoint.read_text())
        checksum = saved.pop("checkpoint_sha256", None)
        if hashlib.sha256(_encoded(saved)).hexdigest() != checksum:
            raise ValueError("checkpoint checksum mismatch")
        if saved["study_sha256"] != identity or saved["code_sha256"] != code:
            raise ValueError("checkpoint belongs to another study or code version")
        committed_bytes = saved["journal_bytes"]
        if type(committed_bytes) is not int or committed_bytes < 0:
            raise ValueError("invalid committed journal length")
        remaining = committed_bytes
        with journal.open("rb") as stream:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("journal is shorter than checkpoint")
                digest.update(chunk)
                remaining -= len(chunk)
        if digest.hexdigest() != saved["journal_sha256"]:
            raise ValueError("committed journal checksum mismatch")
        count = saved["intervals"]
        if type(count) is not int or count < 0:
            raise ValueError("invalid checkpoint interval count")
        engine.restore(saved["engine"])
        with journal.open("r+b") as stream:
            stream.truncate(committed_bytes)
    else:
        if checkpoint.exists() or journal.exists():
            raise FileExistsError("run already exists; use resume or a new directory")
        engine.reset()
        journal.touch()
        write_json(out / "study.json", spec.model_dump(mode="json"))
    def commit(stream):
        stream.flush()
        os.fsync(stream.fileno())
        saved = {"schema_version": "power-grid-env.qsts-checkpoint/v1",
                   "study_sha256": identity, "code_sha256": code,
                   "intervals": count, "journal_bytes": stream.tell(),
                   "journal_sha256": digest.hexdigest(), "engine": engine.snapshot()}
        saved["checkpoint_sha256"] = hashlib.sha256(_encoded(saved)).hexdigest()
        write_json(checkpoint, saved)
        with checkpoint.open("rb") as durable:
            os.fsync(durable.fileno())
        directory_fd = os.open(out, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    status = "completed"
    performed = 0
    with journal.open("ab") as stream:
        commit(stream)
        if engine.last_result is None or engine.last_result.status not in {"converged", "deenergized_no_source"}:
            status = "failed"
        else:
            while engine.time_s < scenario.horizon_s - 1e-9:
                if cancelled is not None and cancelled():
                    status = "cancelled"
                    break
                if max_steps is not None and performed >= max_steps:
                    status = "paused"
                    break
                row = engine.step_control({"kind": "hold"})
                result = engine.last_result
                record = {"interval": count, "step": row,
                          "result": result.model_dump(mode="json", exclude={"script"}),
                          "execution_events": list(engine.execution_log)}
                line = _encoded(record) + b"\n"
                stream.write(line)
                digest.update(line)
                count += 1
                performed += 1
                _compact(engine)
                if count % checkpoint_every == 0:
                    commit(stream)
                if row["solve_status"] not in {"converged", "deenergized_no_source"}:
                    status = "failed"
                    break
        commit(stream)
    report = {"schema_version": "power-grid-env.qsts-result/v1", "study_id": spec.id,
              "status": status, "solved": status == "completed", "completed": status == "completed",
              "intervals": count, "expected_intervals": math.ceil(scenario.horizon_s / scenario.control_interval_s),
              "time_s": engine.time_s, "horizon_s": scenario.horizon_s,
              "integrals": engine.total_integrals(), "soc": engine.soc,
              "study_sha256": identity, "code_sha256": code,
              "journal_sha256": digest.hexdigest(), "journal_bytes": journal.stat().st_size,
              "retained_trace_steps": len(engine.trace), "retained_history_steps": len(engine.history),
              "retained_measurements": len(engine.measurements),
              "last_result": None if engine.last_result is None else engine.last_result.model_dump(mode="json", exclude={"script"}),
              "policy": "hold", "field_validated": False, "industrial_profile_qualified": False}
    write_json(out / "result.json", report)
    return report


def _compact(engine):
    # All actions/events have already been written to the privileged journal.
    engine.history[:] = engine.history[-1:]
    engine.execution_log.clear()
    latest = {}
    future = []
    for sample in engine.measurements:
        if sample["available_s"] <= engine.time_s + 1e-12:
            latest[sample["meter_id"]] = sample
        else:
            future.append(sample)
    engine.measurements[:] = [*latest.values(), *future]
