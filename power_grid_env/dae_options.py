"""Explicit resource and linear-solve controls for the index-1 DAE backend."""
from pydantic import BaseModel,ConfigDict,Field


class DAEOptions(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    segment_duration_s: float=Field(default=.1,ge=1e-6,le=10.)
    krylov_rtol: float=Field(default=1e-11,gt=0,le=1e-5)
    krylov_atol: float=Field(default=1e-14,gt=0,le=1e-8)
    krylov_restart: int=Field(default=30,ge=2,le=128)
    krylov_max_cycles: int=Field(default=8,ge=1,le=64)
    block_size: int=Field(default=32,ge=1,le=32)
    maximum_linear_retries: int=Field(default=8,ge=0,le=32)
    maximum_linear_iterations: int=Field(default=200000,ge=1,le=2000000)
    maximum_jvp_evaluations: int=Field(default=1000000,ge=1,le=10000000)
    maximum_segment_retained_scalars: int=Field(default=2000000,ge=1,le=20000000)
