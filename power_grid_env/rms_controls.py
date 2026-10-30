"""Explicit machine-base controller passports; no hidden model tuning."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TGOV1Passport(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    model: Literal['TGOV1'] = 'TGOV1'
    R: float = Field(gt=0)
    T1: float = Field(gt=0)
    T2: float = Field(ge=0)
    T3: float = Field(gt=0)
    VMIN: float
    VMAX: float
    Dt: float = Field(ge=0)

    @model_validator(mode='after')
    def limits(self):
        if self.VMIN >= self.VMAX:raise ValueError('VMIN must be below VMAX')
        return self


class SEXSPassport(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    model: Literal['SEXS'] = 'SEXS'
    TATB: float = Field(ge=0)
    TB: float = Field(gt=0)
    K: float = Field(gt=0)
    TE: float = Field(gt=0)
    EMIN: float
    EMAX: float

    @model_validator(mode='after')
    def limits(self):
        if self.EMIN >= self.EMAX:raise ValueError('EMIN must be below EMAX')
        return self


class IEEESTPassport(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    model: Literal['IEEEST'] = 'IEEEST'
    MODE: Literal[1] = 1
    A1: float = Field(gt=0)
    A2: float = Field(gt=0)
    A3: float = Field(gt=0)
    A4: float = Field(gt=0)
    A5: float = Field(ge=0)
    A6: float = Field(ge=0)
    T1: float = Field(ge=0)
    T2: float = Field(gt=0)
    T3: float = Field(ge=0)
    T4: float = Field(gt=0)
    T5: float = Field(gt=0)
    T6: float = Field(gt=0)
    KS: float = Field(ge=0)
    LSMIN: float = Field(le=0)
    LSMAX: float = Field(gt=0)
    VCL: float = Field(gt=0)
    VCU: float = Field(gt=0)

    @model_validator(mode='after')
    def limits(self):
        if self.VCL >= self.VCU:raise ValueError('VCL must be below VCU')
        return self
