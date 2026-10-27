"""Декларация сценария. Скрытые поля не входят в public_task_config."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "power-grid-env.scenario/v1"
    id: str
    seed: int = 1
    horizon_s: float = 300.0
    control_interval_s: float = 60.0
    settle_s: float = 60.0
    v_min_pu: float = 0.95
    v_max_pu: float = 1.05
    radiality: bool = True
    fault_line_id: str | None = None
    fault_kind: str = "3ph"
    fault_phases: list[str] = Field(default_factory=lambda: ["A", "B", "C"])
    fault_r_ohm: float = 1.0
    fault_disclosed: bool = True
    hidden_canary: str | None = None
    unrestorable_load_ids: list[str] = Field(default_factory=list)
    meter_delay_s: dict[str, float] = Field(default_factory=dict)
    drop_probability: float = 0.0
    measurement_period_s: float | None = None
    protection: list[dict[str, Any]] = Field(default_factory=list)
    load_profile: dict[str, list[list[float]]] = Field(default_factory=dict)
    trace_limit: int | None = None
    what_if_quota: int = 4
    maintenance_switch_ids: list[str] = Field(default_factory=list)
    model_mismatch_scale: dict[str, float] | None = None
    description: str = ""
    difficulty: str = "nominal"

    @model_validator(mode="after")
    def _bounds(self) -> "Scenario":
        if self.control_interval_s <= 0:
            raise ValueError("control_interval_s должен быть положительным")
        if self.horizon_s <= 0:
            raise ValueError("horizon_s должен быть положительным")
        if self.settle_s < 0:
            raise ValueError("settle_s не может быть отрицательным")
        if not 0.0 <= self.drop_probability <= 1.0:
            raise ValueError("drop_probability вне [0, 1]")
        if self.measurement_period_s is not None and self.measurement_period_s <= 0:
            raise ValueError("measurement_period_s должен быть положительным")
        if self.fault_r_ohm < 0:
            raise ValueError("fault_r_ohm не может быть отрицательным")
        if self.trace_limit is not None and self.trace_limit < 1:
            raise ValueError("trace_limit должен быть положительным")
        return self

    def public_config(self, network_dump: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "power-grid-env.public-task/v1",
            "scenario_id": self.id,
            "description": self.description,
            "horizon_s": self.horizon_s,
            "control_interval_s": self.control_interval_s,
            "v_min_pu": self.v_min_pu,
            "v_max_pu": self.v_max_pu,
            "radiality": self.radiality,
            "known_fault_section": self.fault_line_id if self.fault_disclosed else None,
            "what_if_quota": self.what_if_quota,
            "network": network_dump,
        }
