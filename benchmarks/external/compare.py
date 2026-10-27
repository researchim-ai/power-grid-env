"""Сравнение прямого native-режима и режима после штатного импорта.

Пороги взяты из плана до просмотра IEEE13 и здесь не ослабляются.
Если импорт неполон, вердикт unsupported, а не pass.
"""

from __future__ import annotations

import math
from typing import Any

VOLTAGE_PU = 1e-5
ANGLE_RAD = 1e-4
# Внешний P/Q/I: abs(error)/Sbase и abs(error)/Ibase. Пол 1e-3 Вт относится к потерям и малым авторским схемам.
POWER_RELATIVE = 1e-5
POWER_FLOOR_W = 1e-3
CURRENT_RELATIVE = 1e-5
TAP_ABS = 1e-6
REQUIRED_NODES = {1, 2, 3, 4}


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
    missing_import = [item for item in sorted(set(native_index) - set(imported_index)) if item[1] in REQUIRED_NODES]
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


def compare_losses(native_w: float | None, native_var: float | None, ours_w: float, ours_var: float) -> dict[str, Any]:
    """Потери линий и трансформаторов. Порог объявлен в плане, не по результату IEEE."""
    if native_w is None or native_var is None or not _finite(ours_w) or not _finite(ours_var):
        return {"verdict": "fail", "reasons": ["nonfinite"], "tolerance_w": None}
    reasons = []
    detail = {}
    for label, native, ours in (("p_w", native_w, ours_w), ("q_var", native_var, ours_var)):
        tolerance = max(POWER_FLOOR_W, POWER_RELATIVE * abs(float(native)))
        error = abs(float(ours) - float(native))
        detail[label] = {"error": error, "tolerance": tolerance}
        if error > tolerance:
            reasons.append(label)
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, **detail}


def compare_terminals(
    reference: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    *,
    s_base_va: float,
    default_ll_v: float,
) -> dict[str, Any]:
    """P, Q и ток проводника. Допуск внешнего плана: 1e-5 от Sbase и Ibase уровня напряжения.

    Узел 0 эталона — земля, фаза G. Пол 1e-3 Вт здесь не подменяет Sbase.
    """
    from power_grid_env.conventions import i_base

    left, dup_left = _index_terminals(reference)
    right, dup_right = _index_terminals(actual)
    reasons: list[str] = []
    if s_base_va <= 0 or default_ll_v <= 0:
        reasons.append("invalid_base")
    power_tolerance = POWER_RELATIVE * float(s_base_va) if s_base_va > 0 else None
    if dup_left or dup_right:
        reasons.append("duplicate_target")
    if not left or not right:
        reasons.append("no_compared_targets")
    missing = sorted(set(left) - set(right))
    if missing:
        reasons.append("missing_required_target")
    nonfinite = 0
    power_errors: list[float] = []
    current_errors: list[float] = []
    worst: list[dict[str, Any]] = []
    for key in sorted(set(left) & set(right)):
        native = left[key]
        ours = right[key]
        for field in ("p_w", "q_var"):
            native_value = native.get(field)
            our_value = ours.get(field)
            if not _finite(native_value) or not _finite(our_value) or power_tolerance is None:
                nonfinite += 1
                continue
            error = abs(float(our_value) - float(native_value))
            power_errors.append(error)
            if error > power_tolerance:
                reasons.append("power")
                worst.append({"equipment": key[1], "terminal": key[2], "conductor": key[3], "phase": key[4], "field": field, "error": error, "tolerance": power_tolerance})
        native_i = complex(native.get("i_real_a", float("nan")), native.get("i_imag_a", float("nan")))
        our_i = complex(ours.get("i_real_a", float("nan")), ours.get("i_imag_a", float("nan")))
        if not (math.isfinite(native_i.real) and math.isfinite(native_i.imag) and math.isfinite(our_i.real) and math.isfinite(our_i.imag)):
            nonfinite += 1
            continue
        ll_v = native.get("ll_v") if _finite(native.get("ll_v")) and float(native["ll_v"]) > 0 else default_ll_v
        try:
            tolerance_i = CURRENT_RELATIVE * i_base(float(ll_v), float(s_base_va))
        except ValueError:
            nonfinite += 1
            continue
        error_i = abs(our_i - native_i)
        current_errors.append(error_i)
        if error_i > tolerance_i:
            reasons.append("current")
            worst.append({"equipment": key[1], "terminal": key[2], "conductor": key[3], "phase": key[4], "field": "i_a", "error": error_i, "tolerance": tolerance_i})
    if nonfinite:
        reasons.append("nonfinite")
    unique = list(dict.fromkeys(reasons))
    worst.sort(key=lambda item: item["error"] / item["tolerance"] if item["tolerance"] else item["error"], reverse=True)
    return {
        "verdict": "fail" if unique else "pass",
        "reasons": unique,
        "compared": len(set(left) & set(right)),
        "missing_required": len(missing),
        "missing_sample": [list(item) for item in missing[:12]],
        "max_power_error_w": max(power_errors) if power_errors else None,
        "max_current_error_a": max(current_errors) if current_errors else None,
        "s_base_va": s_base_va,
        "power_tolerance_w": power_tolerance,
        "worst": worst[:8],
    }


def compare_taps(reference: list[dict[str, Any]], actual: list[dict[str, Any]]) -> dict[str, Any]:
    """Положение второй обмотки. Сеть без трансформаторов не требует отпаек."""
    left: dict[str, float] = {}
    right: dict[str, float] = {}
    duplicate = False
    for bucket, rows in ((left, reference), (right, actual)):
        for row in rows:
            key = str(row.get("equipment", "")).lower()
            if key in bucket:
                duplicate = True
            value = row.get("tap")
            if _finite(value):
                bucket[key] = float(value)
    if not left and not right:
        return {"verdict": "pass", "reasons": [], "compared": 0, "max_abs": 0.0, "missing_required": 0}
    reasons: list[str] = []
    if duplicate:
        reasons.append("duplicate_target")
    if not left or not right:
        reasons.append("no_compared_targets")
    missing = sorted(set(left) - set(right))
    if missing:
        reasons.append("missing_required_target")
    errors = [abs(right[key] - left[key]) for key in sorted(set(left) & set(right))]
    if any(not _finite(row.get("tap")) for row in [*reference, *actual]):
        reasons.append("nonfinite")
    max_abs = max(errors) if errors else None
    if max_abs is not None and max_abs > TAP_ABS:
        reasons.append("tap")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "compared": len(errors),
        "missing_required": len(missing),
        "missing_sample": missing[:12],
        "max_abs": max_abs,
        "tolerance": TAP_ABS,
    }


def _index_terminals(rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, str, int, int, str], dict[str, Any]], bool]:
    found: dict[tuple[str, str, int, int, str], dict[str, Any]] = {}
    duplicate = False
    for row in rows:
        key = (
            str(row.get("class", "")).lower(),
            str(row.get("equipment", "")).lower(),
            int(row.get("terminal", 0)),
            int(row.get("conductor", 0)),
            str(row.get("phase", "")),
        )
        if key in found:
            duplicate = True
        found[key] = row
    return found, duplicate


def compare_power(reference: list[dict[str, Any]], actual: list[dict[str, Any]], *, tolerance_w: float = 1.0) -> dict[str, Any]:
    """Сравнение P/Q по оборудованию. Пустой список и NaN не проходят."""
    def index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        found = {}
        duplicate = False
        for row in rows:
            key = (str(row.get("equipment", "")).lower(), str(row.get("phase", "")))
            if key in found:
                duplicate = True
            found[key] = row
        return found, duplicate

    left, dup_left = index(reference)
    right, dup_right = index(actual)
    reasons = []
    if dup_left or dup_right:
        reasons.append("duplicate_target")
    if not left or not right:
        reasons.append("no_compared_targets")
    errors = []
    nonfinite = 0
    for key in sorted(set(left) & set(right)):
        for field in ("p_w", "q_var"):
            a = left[key].get(field)
            b = right[key].get(field)
            if not _finite(a) or not _finite(b):
                nonfinite += 1
                continue
            errors.append(abs(float(a) - float(b)))
    if nonfinite:
        reasons.append("nonfinite")
    missing = sorted(set(left) - set(right))
    if missing:
        reasons.append("missing_required_target")
    worst = max(errors) if errors else None
    if worst is not None and worst > tolerance_w:
        reasons.append("power")
    if not errors and "no_compared_targets" not in reasons:
        reasons.append("no_compared_targets")
    return {
        "verdict": "fail" if reasons else "pass",
        "reasons": reasons,
        "compared": len(errors) // 2,
        "max_abs_w": worst,
        "tolerance_w": tolerance_w,
    }


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _wrap(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi
