"""Выгрузка публичных JSON Schema из моделей pydantic."""

from __future__ import annotations

import json
from pathlib import Path

from power_grid_env.model import Network
from power_grid_env.scenarios import Scenario


def export_schemas(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "network.schema.json").write_text(
        json.dumps(Network.model_json_schema(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (directory / "scenario.schema.json").write_text(
        json.dumps(Scenario.model_json_schema(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
