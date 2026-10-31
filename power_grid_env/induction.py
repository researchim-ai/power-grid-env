"""Balanced single-cage and physical two-parallel-cage motor passports.

Stator transients, saturation, continuous skin-effect laws and converter controls are absent.
Motor torque and all electrical pu parameters use each motor's own MVA base.
"""
from __future__ import annotations

import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from power_grid_env.motor_thermal import MotorThermalProtection
from power_grid_env.motor_contactor import MotorContactorControl


class MotorMechanicalLoad(BaseModel):
    """Dissipative load; Coulomb friction is explicitly regularized near zero."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    model: Literal['polynomial_with_smooth_friction'] = 'polynomial_with_smooth_friction'
    viscous_torque_pu: float = Field(default=0., ge=0)
    quadratic_torque_pu: float = Field(default=0., ge=0)
    friction_torque_pu: float = Field(default=0., ge=0)
    smoothing_speed_pu: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode='after')
    def explicit_friction(self):
        if self.friction_torque_pu > 0 and self.smoothing_speed_pu is None:
            raise ValueError('smooth friction requires an explicit speed scale')
        if self.friction_torque_pu == 0 and self.smoothing_speed_pu is not None:
            raise ValueError('speed smoothing without friction is ambiguous')
        return self

    def torque(self, speed):
        value = self.viscous_torque_pu*speed+self.quadratic_torque_pu*speed*abs(speed)
        if self.friction_torque_pu:
            value += self.friction_torque_pu*math.tanh(speed/self.smoothing_speed_pu)
        return value


class MotorOperatingPoint(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    method: Literal['high_speed_equilibrium'] = 'high_speed_equilibrium'
    minimum_speed_pu: float = Field(default=.8, gt=0, lt=1)
    tolerance: float = Field(default=1e-8, gt=0, le=1e-6)
    thermal_state: Literal['passport', 'equilibrium'] = 'passport'


class InductionMotor(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1)
    model: Literal['single_cage','double_cage'] = 'single_cage'
    rotor_mutual_x_pu: float = Field(default=0.,ge=0)
    rotor2_r_pu: float | None = Field(default=None,gt=0)
    rotor2_x_pu: float | None = Field(default=None,gt=0)
    node_id: str
    rated_va: float = Field(gt=0)
    rated_ll_v: float = Field(gt=0)
    stator_r_pu: float = Field(gt=0)
    stator_x_pu: float = Field(gt=0)
    rotor_r_pu: float = Field(gt=0)
    rotor_x_pu: float = Field(gt=0)
    magnetizing_x_pu: float = Field(gt=0)
    inertia_s: float = Field(gt=0)
    fan_torque_pu: float | None = Field(default=None, ge=0)
    mechanical_load: MotorMechanicalLoad | None = None
    connected: bool = True
    thermal: MotorThermalProtection | None = None
    contactor: MotorContactorControl | None = None

    @model_validator(mode='after')
    def single_mechanical_passport(self):
        if self.model=='double_cage' and (self.rotor2_r_pu is None or self.rotor2_x_pu is None):raise ValueError('double cage requires both second rotor parameters')
        if self.model=='single_cage' and (self.rotor2_r_pu is not None or self.rotor2_x_pu is not None or self.rotor_mutual_x_pu!=0.):raise ValueError('second rotor parameters require explicit double_cage model')
        if (self.fan_torque_pu is None) == (self.mechanical_load is None):
            raise ValueError('supply exactly one of fan_torque_pu or mechanical_load')
        return self

    def load_torque(self, speed):
        if self.mechanical_load is not None:
            return self.mechanical_load.torque(speed)
        return self.fan_torque_pu*speed*abs(speed)

    def constants(self, frequency_hz):
        """Norton reactance and historical first-cage reference X/T values.

        The final two scalars do not define two-cage dynamics; that model uses
        the full matrix block in MotorCageBank.
        """
        total = self.rotor_x_pu + self.magnetizing_x_pu
        x_prime = self.stator_x_pu + self.rotor_x_pu * self.magnetizing_x_pu / total
        difference = self.magnetizing_x_pu * (self.magnetizing_x_pu / total)
        tau = total / (2 * math.pi * frequency_hz * self.rotor_r_pu)
        if self.model=='double_cage':
            from power_grid_env.motor_cages import double_cage_coefficients
            x_prime=double_cage_coefficients(self,frequency_hz)[0]
        return x_prime, difference, tau


class InductionEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    time_s: float = Field(gt=0)
    kind: Literal['source_voltage', 'motor_open', 'motor_close', 'load_torque_scale']
    equipment_id: str
    value: float | None = None

    @model_validator(mode='after')
    def value_kind(self):
        if self.kind in {'source_voltage', 'load_torque_scale'}:
            if self.value is None or self.value < 0:
                raise ValueError('voltage/load event requires a nonnegative absolute scale')
        elif self.value is not None:
            raise ValueError('contactor event takes no value')
        return self


class InductionSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    duration_s: float = Field(default=5., gt=0, le=60)
    step_s: float = Field(default=.005, gt=0)
    rtol: float = Field(default=1e-7, gt=0, le=1e-4)
    atol: float = Field(default=1e-9, gt=0, le=1e-6)
    motors: list[InductionMotor] = Field(min_length=1, max_length=32)
    operating_point: MotorOperatingPoint | None = None
    maximum_contactor_transitions: int = Field(default=64, ge=1, le=512)
    events: list[InductionEvent] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def budgets(self):
        if self.step_s > self.duration_s or math.ceil(self.duration_s / self.step_s) > 200000:
            raise ValueError('invalid motor integration sampling budget')
        if len({m.id for m in self.motors}) != len(self.motors):
            raise ValueError('duplicate motor ID')
        if any(e.time_s >= self.duration_s for e in self.events):
            raise ValueError('events must precede horizon')
        keys = [(e.time_s, 'source' if e.kind == 'source_voltage' else 'load' if e.kind == 'load_torque_scale' else 'motor' if e.kind in {'motor_open','motor_close'} else e.kind, e.equipment_id) for e in self.events]
        if len(set(keys)) != len(keys):
            raise ValueError('simultaneous commands to the same device are ambiguous')
        return self
