"""Сравнение прямого native-режима и режима после штатного импорта.

Пороги взяты из плана до просмотра IEEE13 и здесь не ослабляются.
Если импорт неполон, вердикт unsupported, а не pass.
"""

from __future__ import annotations

import math
from typing import Any

VOLTAGE_PU = 1e-5
ANGLE_RAD = 1e-4


def compare_buses(native: dict[str, Any], imported_rows: list[dict[str, Any]]) -> dict[str, Any]:
    native_index = {}
    for bus in native.get("buses") or []:
        for phase in bus["phases"]:
            native_index[(bus["name"].lower(), int(phase["node"]))] = phase
    imported_index = {(row["bus"].lower(), int(row["node"])): row for row in imported_rows}
    keys = sorted(set(native_index) & set(imported_index))
    missing_native = sorted(set(imported_index) - set(native_index))
    missing_import = [item for item in sorted(set(native_index) - set(imported_index)) if item[1] in {1, 2, 3}]
    voltage_errors = []
    angle_errors = []
    worst = []
    for key in keys:
        reference = native_index[key]
        actual = imported_index[key]
        if reference["pu"] < 1e-3:
            continue
        voltage_error = abs(actual["pu"] - reference["pu"])
        angle_error = abs(_wrap(math.radians(actual["angle_deg"] - reference["angle_deg"])))
        voltage_errors.append(voltage_error)
        angle_errors.append(angle_error)
        worst.append({"bus": key[0], "node": key[1], "voltage_pu": voltage_error, "angle_rad": angle_error})
    worst.sort(key=lambda item: item["voltage_pu"], reverse=True)
    max_v = max(voltage_errors) if voltage_errors else None
    max_a = max(angle_errors) if angle_errors else None
    if not native.get("converged"):
        verdict = "data_unavailable" if native.get("error") else "fail"
    elif not imported_rows:
        verdict = "unsupported"
    elif missing_import or (max_v is not None and (max_v > VOLTAGE_PU or (max_a or 0) > ANGLE_RAD)):
        verdict = "fail"
    else:
        verdict = "pass"
    return {
        "evidence": "conversion_consistency",
        "thresholds": {"voltage_pu": VOLTAGE_PU, "angle_rad": ANGLE_RAD},
        "compared": len(keys),
        "missing_in_native": missing_native[:20],
        "missing_in_import": [list(item) for item in missing_import[:40]],
        "max_voltage_error_pu": max_v,
        "max_angle_error_rad": max_a,
        "worst": worst[:8],
        "verdict": verdict,
        "independence": "один и тот же DSS-Extensions; это проверка адаптера, не внешний решатель",
    }


def _wrap(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi
