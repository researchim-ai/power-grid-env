"""Сохранённое исследование. Неподдержанный объект отклоняется до расчёта."""

from __future__ import annotations

from typing import Any, Callable
import math

from pydantic import BaseModel, ConfigDict, ValidationError, Field

from power_grid_env.scenarios import Scenario
from power_grid_env.results import SolveOptions
from power_grid_env.rms_study import RMSSettings
from power_grid_env.induction import InductionSettings
from power_grid_env.coupled_rms import CoupledSettings

from power_grid_env.model import Network
from power_grid_env.scope import compile_scope

STUDY_SCHEMA = "power-grid-env.study/v1"
_EXECUTABLE = {("power_flow", "opendss"), ("qsts", "opendss"), ("rms", "andes"), ("small_signal", "andes"), ("motor_start", "scipy"), ("coupled_rms", "scipy"), ("coupled_small_signal", "scipy")}


class StudySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = STUDY_SCHEMA
    id: str
    study: str
    backend: str
    network: Network
    requested_motors: list[str] = Field(default_factory=list)
    scenario: Scenario | None = None
    rms: RMSSettings | None = None
    induction: InductionSettings | None = None
    coupled: CoupledSettings | None = None
    options: SolveOptions = Field(default_factory=SolveOptions)


def migrate_study(payload: dict[str, Any]) -> dict[str, Any]:
    """Старое поле kind становится study. Электрическая сеть внутри документа не переписывается."""
    data = dict(payload)
    if "schema_version" not in data and "kind" in data:
        data["study"] = data.pop("kind")
        data["schema_version"] = STUDY_SCHEMA
    return data


def run_study_document(payload: dict[str, Any], solver: Callable[..., Any] | None = None, **kwargs) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _rejected("schema")
    migrated = migrate_study(payload)
    try:
        spec = StudySpec.model_validate(migrated)
    except ValidationError as exc:
        return {"solved": False, "status": "rejected", "reason": "schema", "detail": str(exc)}
    return run_study(spec, solver, **kwargs)


def run_study(spec: StudySpec, solver: Callable[..., Any] | None = None, *, out=None, resume=False, max_steps=None, checkpoint_every=96, cancelled=None, isolate=False, wall_timeout_s=None) -> dict[str, Any]:
    if spec.schema_version != STUDY_SCHEMA:
        return _rejected("schema")
    if spec.requested_motors:
        return _rejected("unsupported_motor")
    if (spec.study, spec.backend) not in _EXECUTABLE:
        return _rejected("unsupported_backend" if spec.backend != "opendss" else "unsupported_study")
    if wall_timeout_s is not None or (spec.study in {"rms", "small_signal", "motor_start", "coupled_rms", "coupled_small_signal"} and cancelled is not None):
        isolate = True
    if isolate and spec.study not in {"rms", "small_signal", "motor_start", "coupled_rms", "coupled_small_signal"}:
        return _rejected("process_isolation_only_for_rms")
    if spec.coupled is not None and spec.study not in {"coupled_rms","coupled_small_signal"}:
        return _rejected("coupled_settings_only_for_coupled_rms")
    if spec.study in {"coupled_rms","coupled_small_signal"}:
        if spec.coupled is None:
            return _rejected("coupled_settings_required")
        if spec.induction is not None or spec.rms is not None or resume or max_steps is not None or spec.scenario is not None or spec.options != SolveOptions():
            return _rejected("unsupported_coupled_runtime_options")
        from power_grid_env.coupled_rms import validate_coupled, run_coupled
        scope=validate_coupled(spec.network,spec.coupled)
        if not scope['ok']:
            return {"solved":False,"completed":False,"status":"rejected","reason":"coupled_scope","scope":scope}
        if isolate:
            from power_grid_env.rms_process import run_isolated_rms
            return run_isolated_rms(spec,timeout_s=120. if wall_timeout_s is None else wall_timeout_s,cancelled=cancelled)
        if spec.study=="coupled_small_signal":
            from power_grid_env.coupled_small_signal import run_coupled_small_signal
            report=run_coupled_small_signal(spec.network,spec.coupled)
        else:report=run_coupled(spec.network,spec.coupled)
        report['study_id']=spec.id
        return report
    if spec.induction is not None and spec.study != "motor_start":
        return _rejected("induction_settings_only_for_motor_start")
    if spec.study == "motor_start":
        if spec.induction is None:
            return _rejected("induction_settings_required")
        if spec.rms is not None or resume or max_steps is not None or spec.scenario is not None or spec.options != SolveOptions():
            return _rejected("unsupported_induction_runtime_options")
        from power_grid_env.induction_network import validate_induction, run_induction
        scope = validate_induction(spec.network, spec.induction)
        if not scope['ok']:
            return {"solved": False, "completed": False, "status": "rejected", "reason": "induction_scope", "scope": scope}
        if isolate:
            from power_grid_env.rms_process import run_isolated_rms
            return run_isolated_rms(spec, timeout_s=120. if wall_timeout_s is None else wall_timeout_s, cancelled=cancelled)
        report = run_induction(spec.network, spec.induction)
        report["study_id"] = spec.id
        return report
    if spec.study in {"rms", "small_signal"}:
        if spec.rms is None:
            return _rejected("rms_settings_required")
        if resume or max_steps is not None or spec.scenario is not None or spec.options != SolveOptions():
            return _rejected("unsupported_rms_runtime_options")
        if isolate:
            from power_grid_env.rms_study import validate_rms
            scope=validate_rms(spec.network,spec.rms)
            if not scope['ok']:
                return {"solved":False,"completed":False,"status":"rejected","reason":"rms_scope","scope":scope}
            from power_grid_env.rms_process import run_isolated_rms
            return run_isolated_rms(spec,timeout_s=120. if wall_timeout_s is None else wall_timeout_s,cancelled=cancelled)
        from power_grid_env.rms_study import run_rms
        try:
            if spec.study == "small_signal":
                from power_grid_env.small_signal import run_small_signal
                report = run_small_signal(spec.network,spec.rms)
            else:
                report = run_rms(spec.network, spec.rms)
            report["study_id"] = spec.id
            return report
        except ImportError as exc:
            return {"solved": False, "completed": False, "status": "backend_unavailable", "error": str(exc)}
    scope = compile_scope(spec.network, "power_flow")
    if not scope["ok"]:
        return _rejected("scope", scope["errors"])
    from power_grid_env.backend_opendss import solve_network

    from power_grid_env.preflight import preflight

    checked = preflight(spec.network, task="power_flow", solve_initial=False)
    if not checked["ok"]:
        return _rejected("preflight", checked["errors"])
    if spec.study == "qsts":
        if spec.scenario is None or out is None:
            return _rejected("qsts_requires_scenario_and_output_directory")
        names = {load.id for load in spec.network.loads}
        for name, points in spec.scenario.load_profile.items():
            if name not in names or not points or not points[0] or points[0][0] != 0:
                return _rejected("invalid_load_profile")
            previous = -1.0
            for row in points:
                if len(row) not in {2, 3} or any(not math.isfinite(v) for v in row):
                    return _rejected("invalid_load_profile")
                if row[0] <= previous:
                    return _rejected("invalid_load_profile")
                previous = row[0]
        from power_grid_env.qsts_study import run_qsts

        # Engine supplies event-specific options (e.g. delayed regulator handling).
        if spec.options != SolveOptions():
            return _rejected("qsts_options_managed_by_engine")
        return run_qsts(spec, out, resume=resume, max_steps=max_steps,
                        checkpoint_every=checkpoint_every, cancelled=cancelled, solver=solver)
    if resume or max_steps is not None:
        return _rejected("resume_only_for_qsts")
    solved = solver(spec.network) if solver else solve_network(spec.network, spec.options)
    return {
        "solved": True,
        "status": solved.status,
        "study_id": spec.id,
        "backend": spec.backend,
        "iterations": solved.iterations,
        "completed": solved.status in {"converged", "deenergized_no_source"},
        "scope": scope,
        "preflight": checked,
        "result": solved.model_dump(mode="json", exclude={"script"}),
        "field_validated": False,
        "industrial_profile_qualified": False,
    }


def _rejected(reason: str, errors: list[str] | None = None) -> dict[str, Any]:
    return {"solved": False, "status": "rejected", "reason": reason, "errors": errors or []}
