"""First-order stator-current thermal replica, not winding hot-spot prediction.

Theta=1 is the steady heating at rated motor current. Copper resistance is
constant; rotor heating, ambient variation and inverse-time relay standards
are not represented by this declared model.
"""
from __future__ import annotations

import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MotorThermalProtection(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    model: Literal['stator_i2_replica'] = 'stator_i2_replica'
    time_constant_s: float = Field(gt=0)
    trip_level: float = Field(gt=0)
    initial_level: float = Field(default=0., ge=0)

    @model_validator(mode='after')
    def initial_below_trip(self):
        if self.initial_level >= self.trip_level:
            raise ValueError('initial thermal level must be below trip threshold')
        return self


def constant_current_level(initial, current_pu, elapsed_s, time_constant_s):
    """Exact replica level for a constant RMS current segment, including cooling."""
    if not all(math.isfinite(x) for x in (initial, current_pu, elapsed_s, time_constant_s)) or initial < 0 or current_pu < 0 or elapsed_s < 0 or time_constant_s <= 0:
        raise ValueError('finite nonnegative levels/current/time and positive tau required')
    decay = math.exp(-elapsed_s/time_constant_s)
    return initial*decay+current_pu**2*(-math.expm1(-elapsed_s/time_constant_s))


def constant_current_trip_time(initial, current_pu, threshold, time_constant_s):
    """Analytic first crossing; None if the threshold is only approached or unreachable."""
    constant_current_level(initial, current_pu, 0., time_constant_s)
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError('positive finite threshold required')
    if initial >= threshold:
        return 0.
    heating = current_pu**2
    if heating <= threshold:
        return None
    return time_constant_s*math.log1p((threshold-initial)/(heating-threshold))
