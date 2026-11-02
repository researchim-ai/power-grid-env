"""Deterministic work/retained-output caps and integration wall deadline.

The outer isolated-worker deadline also covers initialization and serialization.
These in-process limits cover the shared RMS integration driver itself.
"""
import time
from pydantic import BaseModel, ConfigDict, Field


class RuntimeLimits(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    maximum_network_buses: int=Field(default=2000,ge=1,le=20000)
    maximum_rhs_evaluations: int=Field(default=2_000_000,ge=1,le=20_000_000)
    maximum_event_batches: int=Field(default=1024,ge=1,le=10000)
    maximum_retained_scalars: int=Field(default=2_000_000,ge=1,le=2_000_000)
    maximum_integration_wall_s: float=Field(default=120.,gt=0,le=3600.)


class RuntimeLimitExceeded(Exception):
    def __init__(self,resource,limit,attempted):
        self.resource=resource;self.limit=limit;self.attempted=attempted
        super().__init__(f'{resource}: attempted {attempted}, limit {limit}')


class IntegrationBudget:
    def __init__(self,limits,clock=None):
        self.limits=limits;self.clock=clock or time.monotonic;self.started=self.clock()
        self.rhs_evaluations=0;self.event_batches=0;self.retained_scalars=0

    def check_time(self):
        elapsed=self.clock()-self.started
        if elapsed>=self.limits.maximum_integration_wall_s:
            raise RuntimeLimitExceeded('integration_wall_s',self.limits.maximum_integration_wall_s,elapsed)

    def rhs(self,callback):
        def counted(t,state):
            self.check_time()
            attempt=self.rhs_evaluations+1
            if attempt>self.limits.maximum_rhs_evaluations:raise RuntimeLimitExceeded('rhs_evaluations',self.limits.maximum_rhs_evaluations,attempt)
            self.rhs_evaluations=attempt
            return callback(t,state)
        return counted

    def event_batch(self):
        self.check_time();attempt=self.event_batches+1
        if attempt>self.limits.maximum_event_batches:raise RuntimeLimitExceeded('event_batches',self.limits.maximum_event_batches,attempt)
        self.event_batches=attempt

    def record(self,scalars):
        self.check_time();attempt=self.retained_scalars+scalars
        if attempt>self.limits.maximum_retained_scalars:raise RuntimeLimitExceeded('retained_scalars',self.limits.maximum_retained_scalars,attempt)
        self.retained_scalars=attempt

    def report(self):
        return dict(rhs_evaluations=self.rhs_evaluations,event_batches=self.event_batches,
                    retained_scalar_slots=self.retained_scalars,integration_elapsed_s=self.clock()-self.started,
                    limits=self.limits.model_dump(mode='json'),scope='integration only; outer worker bounds complete study')
