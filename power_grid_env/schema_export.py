"""Выгрузка публичных JSON Schema из моделей pydantic."""

from __future__ import annotations

import json
from pathlib import Path

from power_grid_env.model import Network
from power_grid_env.scenarios import Scenario


def export_schemas(directory: Path) -> None:
    from power_grid_env.study_spec import StudySpec

    directory.mkdir(parents=True, exist_ok=True)
    from power_grid_env.motor_sensitivity import MotorSensitivityProtocol
    (directory / "motor-sensitivity.schema.json").write_text(
        json.dumps(MotorSensitivityProtocol.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
    from power_grid_env.motor_trace import MotorTraceProtocol
    (directory / "motor-trace.schema.json").write_text(
        json.dumps(MotorTraceProtocol.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "study.schema.json").write_text(
        json.dumps(StudySpec.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "network.schema.json").write_text(
        json.dumps(Network.model_json_schema(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (directory / "scenario.schema.json").write_text(
        json.dumps(Scenario.model_json_schema(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
