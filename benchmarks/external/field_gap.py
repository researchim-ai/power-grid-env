"""Пробел натурной сверки. Пороги зафиксированы и ни к чему не применены."""

from __future__ import annotations

from pathlib import Path

from benchmarks.external._common import write_json

REQUIRED = (
    "topology_on_each_interval",
    "equipment_parameters",
    "independent_inputs",
    "independent_targets",
    "meter_mapping",
    "timestamps",
    "quality_flags",
    "usage_terms",
)

# План §6.3. Числа не подгонялись: измерений, к которым их можно приложить, нет.
FROZEN_PILOT_THRESHOLDS = {
    "voltage_mae_pu": 0.005,
    "voltage_p95_pu": 0.01,
    "feeder_pq_nmae": 0.05,
    "daily_energy_error": 0.02,
    "usable_interval_fraction": 0.95,
}


def assessment() -> dict:
    return {
        "verdict": "not_assessed_missing_paired_measurements",
        "score_computed": False,
        "validated_for_declared_scope": False,
        "request_sent": False,
        "required": list(REQUIRED),
        "present": [],
        "thresholds_frozen_not_applied": FROZEN_PILOT_THRESHOLDS,
        "note": (
            "Нет пакета, где топология, независимые входы и независимые цели сняты на одном интервале. "
            "Пороги пилота записаны заранее и не использовались для оценки."
        ),
    }


def write_report(out: Path) -> dict:
    payload = assessment()
    write_json(out / "gap.json", payload)
    return payload
