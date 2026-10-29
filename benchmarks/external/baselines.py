"""Сводка сравнений с другими открытыми инструментами.

Совпадение с тем же OpenDSS не доказывает превосходство. Невыполненный прогон остаётся not_performed.
"""

from __future__ import annotations

from typing import Any


def baseline_report() -> dict[str, Any]:
    return {
        "competitive_superiority_proven": False,
        "candidates": [
            {
                "id": "direct-dss",
                "comparison_run": "conversion_consistency",
                "note": "тот же DSS-Extensions, что и адаптер; это не независимый решатель",
            },
            {"id": "pydss-disco", "comparison_run": "not_performed"},
            {"id": "pandapower", "comparison_run": "partial_simbench_voltage_only"},
            {"id": "power-grid-model", "comparison_run": "not_performed"},
            {"id": "grid2op", "comparison_run": "not_performed"},
            {"id": "andes", "comparison_run": "two_bus_power_flow"},
        ],
    }
