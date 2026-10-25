"""Экспорт разрешённых наблюдений. Нормализация обучающей выборки здесь не оценивается."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def export_traces(traces: list[dict[str, Any]], path: Path, *, scenario_id: str, model_hash: str) -> None:
    rows = []
    for trace in traces:
        for step in trace:
            observation = step["observation"]
            rows.append(
                {
                    "scenario_id": scenario_id,
                    "time_s": float(step["time_s"]),
                    "observation_json": json.dumps(observation, ensure_ascii=False),
                    "action_json": json.dumps(step.get("action"), ensure_ascii=False),
                    "reward": float(step.get("reward") or 0.0),
                    "model_hash": model_hash,
                    "provenance": "permitted-observation",
                }
            )
    table = pa.Table.from_pylist(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
