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
    """Пустой набор, NaN и пропущенный обязательный узел не получают pass.

    У обесточенного узла сравнивается модуль. Угол при |V| < 1e-3 не оценивается.
    """
    reasons: list[str] = []
    native_index: dict[tuple[str, int], dict[str, Any]] = {}
    duplicate_native = False
    for bus in native.get("buses") or []:
        for phase in bus.get("phases") or []:
            key = (str(bus.get("name", "")).lower(), int(phase["node"]))
            if key in native_index:
                duplicate_native = True
            native_index[key] = phase
    imported_index: dict[tuple[str, int], dict[str, Any]] = {}
    duplicate_import = False
    for row in imported_rows:
        key = (str(row["bus"]).lower(), int(row["node"]))
        if key in imported_index:
            duplicate_import = True
        imported_index[key] = row
    if duplicate_native or duplicate_import:
        reasons.append("duplicate_target")
    keys = sorted(set(native_index) & set(imported_index))
    missing_native = sorted(set(imported_index) - set(native_index))
    missing_import = [item for item in sorted(set(native_index) - set(imported_index)) if item[1] in {1, 2, 3}]
    voltage_errors: list[float] = []
    angle_errors: list[float] = []
    worst: list[dict[str, Any]] = []
    nonfinite = 0
    angle_skipped = 0
    for key in keys:
        reference = native_index[key]
        actual = imported_index[key]
        reference_pu = reference.get("pu")
        actual_pu = actual.get("pu")
        if not _finite(reference_pu) or not _finite(actual_pu):
            nonfinite += 1
            continue
        voltage_error = abs(float(actual_pu) - float(reference_pu))
        voltage_errors.append(voltage_error)
        angle_error = None
        if float(reference_pu) >= 1e-3 and float(actual_pu) >= 1e-3:
            if _finite(reference.get("angle_deg")) and _finite(actual.get("angle_deg")):
                angle_error = abs(_wrap(math.radians(float(actual["angle_deg"]) - float(reference["angle_deg"]))))
                angle_errors.append(angle_error)
            else:
                nonfinite += 1
        else:
            angle_skipped += 1
        worst.append(
            {
                "bus": key[0],
                "node": key[1],
                "voltage_pu": voltage_error,
                "angle_rad": angle_error,
            }
        )
    worst.sort(key=lambda item: item["voltage_pu"], reverse=True)
    max_v = max(voltage_errors) if voltage_errors else None
    max_a = max(angle_errors) if angle_errors else None
    if not native.get("converged"):
        reasons.append("native_not_converged")
    if not keys or not voltage_errors:
        reasons.append("no_compared_targets")
    if missing_import:
        reasons.append("missing_required_target")
    if nonfinite:
        reasons.append("nonfinite")
    if max_v is not None and max_v > VOLTAGE_PU:
        reasons.append("voltage")
    if max_a is not None and max_a > ANGLE_RAD:
        reasons.append("angle")
    if native.get("error") and "native_not_converged" in reasons and not native.get("buses"):
        verdict = "data_unavailable"
    elif reasons:
        verdict = "fail"
    else:
        verdict = "pass"
    return {
        "evidence": "conversion_consistency",
        "thresholds": {"voltage_pu": VOLTAGE_PU, "angle_rad": ANGLE_RAD},
        "counts": {
            "native_targets": len(native_index),
            "imported_targets": len(imported_index),
            "compared": len(voltage_errors),
            "missing_required": len(missing_import),
            "nonfinite": nonfinite,
            "angle_skipped": angle_skipped,
        },
        "compared": len(voltage_errors),
        "missing_in_native": [list(item) for item in missing_native[:20]],
        "missing_in_import": [list(item) for item in missing_import[:40]],
        "max_voltage_error_pu": max_v,
        "max_angle_error_rad": max_a,
        "reasons": reasons,
        "worst": worst[:8],
        "verdict": verdict,
        "independence": "один и тот же DSS-Extensions; это проверка адаптера, не внешний решатель",
    }


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _wrap(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi
