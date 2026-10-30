"""Результат расчёта. Численная несходимость не записывается как коллапс."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SolveStatus = Literal[
    "converged",
    "numerically_not_converged",
    "deenergized_no_source",
    "invalid_model",
]


class PhaseVoltage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bus_id: str
    phase: str
    v_real_v: float
    v_imag_v: float
    v_base_ln_v: float
    island_id: str
    energized: bool

    @property
    def complex(self) -> complex:
        return complex(self.v_real_v, self.v_imag_v)


class ConductorFlow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equipment_id: str
    terminal_index: int
    bus_id: str | None
    phase: str
    p_into_w: float
    q_into_var: float
    i_into_real_a: float
    i_into_imag_a: float

    @property
    def current(self) -> complex:
        return complex(self.i_into_real_a, self.i_into_imag_a)

    @property
    def power(self) -> complex:
        return complex(self.p_into_w, self.q_into_var)


class IslandReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    bus_ids: list[str]
    source_ids: list[str]
    energized: bool
    reason: str


class SolveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SolveStatus
    converged: bool
    iterations: int
    algorithm: str
    tolerance: float
    backend_name: str
    backend_version: str
    voltages: list[PhaseVoltage] = Field(default_factory=list)
    flows: list[ConductorFlow] = Field(default_factory=list)
    islands: list[IslandReport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    script: str = ""
    tap_positions: dict[str, list[float]] = Field(default_factory=dict)
    limited_generators: list[str] = Field(default_factory=list)


class SolveOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tolerance: float = 1e-10
    max_iterations: int = 80
    max_outer: int = 20
    max_pv_refinements: int = Field(default=4, ge=0, le=32)
    apply_regulators: bool = True
    inverter_current_limit_model: bool = False
    approximate_phase_shift: bool = False
