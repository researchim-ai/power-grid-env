"""Analytic duty planning for the declared stator I² replica (not OEM limits).

Currents use the generator's own rated RMS current base. These calculations
assume prescribed, piecewise constant current; they do not predict network current.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from power_grid_env.generator_thermal import GeneratorThermalProtection


class CurrentDuty(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    duration_s: float = Field(gt=0)
    current_pu: float = Field(ge=0)


class ThermalDutyStudy(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    protection: GeneratorThermalProtection
    initial_level: float = Field(ge=0)
    segments: list[CurrentDuty] = Field(min_length=1, max_length=10000)


def _valid(level: float, current: float, tau: float) -> None:
    if not all(math.isfinite(v) for v in (level, current, tau)) or level < 0 or current < 0 or tau <= 0:
        raise ValueError('finite nonnegative level/current and positive time constant required')
    if not math.isfinite(current * current):
        raise ValueError('current squared must be finite')


def _log_ratio_time(delta: float, denominator: float, tau: float) -> float:
    ratio = delta / denominator
    logarithm = (math.log1p(ratio) if math.isfinite(ratio)
                 else math.log(delta) - math.log(denominator))
    result = tau * logarithm
    if not math.isfinite(result):
        raise ValueError('crossing time exceeds floating point range')
    return result


def thermal_level(level: float, current: float, duration: float, tau: float) -> float:
    """Exact segment endpoint; expm1 preserves very short pulse accuracy."""
    _valid(level, current, tau)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError('finite nonnegative duration required')
    return level * math.exp(-duration / tau) + current * current * (-math.expm1(-duration / tau))


def time_to_trip(level: float, current: float, tau: float, threshold: float) -> float | None:
    """None means no finite crossing at constant current, including asymptotic equality."""
    _valid(level, current, tau)
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError('finite positive threshold required')
    if level >= threshold:
        return 0.0
    heat = current * current
    if heat <= threshold:
        return None
    return _log_ratio_time(threshold - level, heat - threshold, tau)


def current_ceiling(level: float, duration: float, tau: float, threshold: float) -> float | None:
    """Current reaching the threshold at horizon; equality trips, so admissible I is lower.

    None means initial level is already at/above threshold, with no available duty.
    """
    _valid(level, 0., tau)
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(threshold) or threshold <= 0:
        raise ValueError('finite positive duration and threshold required')
    if level >= threshold:
        return None
    fraction = -math.expm1(-duration / tau)
    if fraction == 0:
        raise ValueError('duration/time constant is below floating point range')
    squared = level + (threshold - level) / fraction
    if not math.isfinite(squared):
        raise ValueError('current ceiling exceeds floating point range')
    return math.sqrt(squared)


def cooling_time(level: float, target: float, tau: float, current: float = 0.) -> float | None:
    """Time to reach a lower target; no finite time if residual heating prevents it.

    Cooling does not clear the simulator's latched electrical protection.
    """
    _valid(level, current, tau)
    if not math.isfinite(target) or target < 0:
        raise ValueError('finite nonnegative target required')
    if level <= target:
        return 0.
    heat = current * current
    if target <= heat:
        return None
    return _log_ratio_time(level - target, target - heat, tau)


def evaluate(study: ThermalDutyStudy) -> dict:
    p = study.protection
    level = study.initial_level
    elapsed = 0.
    first_trip = 0. if level >= p.trip_level else None
    rows = []
    for duty in study.segments:
        crossing = time_to_trip(level, duty.current_pu, p.time_constant_s, p.trip_level)
        if first_trip is None and crossing is not None and crossing <= duty.duration_s:
            first_trip = elapsed + crossing
        end = thermal_level(level, duty.current_pu, duty.duration_s, p.time_constant_s)
        rows.append(dict(start_s=elapsed, end_s=elapsed + duty.duration_s,
                         current_pu=duty.current_pu, initial_level=level, final_level=end,
                         maximum_level=max(level, end)))
        elapsed += duty.duration_s
        if not math.isfinite(elapsed):
            raise ValueError('total duration exceeds floating point range')
        level = end
    return dict(model=p.model, generator_id=p.generator_id,
                admissible=first_trip is None, first_trip_s=first_trip,
                final_level=level, maximum_level=max(row['maximum_level'] for row in rows),
                segments=rows, scope='prescribed_current_counterfactual_without_breaker_feedback',
                automatic_reclose_permitted=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('study', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = evaluate(ThermalDutyStudy.model_validate_json(args.study.read_text()))
    encoded = json.dumps(result, indent=2, allow_nan=False) + '\n'
    if args.out:
        args.out.write_text(encoded)
    else:
        print(encoded, end='')


if __name__ == '__main__':
    main()
