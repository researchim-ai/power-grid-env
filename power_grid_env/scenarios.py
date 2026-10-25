"""Декларация сценария. Скрытые поля не входят в public_task_config."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    protection: list[dict[str, Any]] = Field(default_factory=list)
    load_profile: dict[str, list[list[float]]] = Field(default_factory=dict)
    what_if_quota: int = 4
    maintenance_switch_ids: list[str] = Field(default_factory=list)
    model_mismatch_scale: dict[str, float] | None = None
    description: str = ""
    difficulty: str = "nominal"

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
