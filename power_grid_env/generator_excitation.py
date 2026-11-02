"""Explicit GENROU field-reaction normalization and droop OEL feedback.

A declared engineering model, not an IEEE/OEM OEL implementation. The measured
quantity is Xad*Ifd, reconstructed from rotor states; it is not SEXS voltage.
"""
from __future__ import annotations
from typing import Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class OverexcitationLimiter(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    generator_id: str = Field(min_length=1)
    model: Literal['field_current_droop_override'] = 'field_current_droop_override'
    rated_field_reaction_pu: float = Field(gt=0)
    current_limit_pu: float = Field(gt=0)
    measurement_time_s: float = Field(gt=0)
    feedback_gain_pu: float = Field(gt=0)
    bias_time_s: float = Field(gt=0)
    maximum_voltage_bias_pu: float = Field(gt=0)


class ExcitationLimiterBank:
    def __init__(self, model, offset):
        self.model = model
        self.offset = offset
        self.entries = {}
        indices = {g.id: i for i, g in enumerate(model.generators)}
        for p in model.settings.overexcitation_limiters:
            self.entries[indices[p.generator_id]] = (offset, p)
            offset += 2
        self.end = offset
        self.overshoot = {}

    def reaction(self, state, current):
        rotor = state[self.model.gen_offset:self.model.rotor_end].reshape(-1, 2)
        return self.model.round_rotors.field_reaction(state, rotor, current)

    def initialize(self, state, current):
        if not self.entries:
            return None
        reaction = self.reaction(state, current)
        for i, (a, p) in self.entries.items():
            value = abs(reaction[i]) / p.rated_field_reaction_pu
            if not np.isfinite(value) or value >= p.current_limit_pu:
                return dict(reason='initial_overexcitation_limit_or_boundary', generator=p.generator_id)
            state[a:a+2] = [value, 0.]
        return None

    def bias(self, state):
        result = np.zeros(len(self.model.generators))
        for i, (a, p) in self.entries.items():
            result[i] = np.clip(state[a+1], 0., p.maximum_voltage_bias_pu)
        return result

    def derivatives(self, state, current):
        result = np.zeros(self.end-self.offset)
        if not self.entries:
            return result
        reaction = self.reaction(state, current)
        for i, (a, p) in self.entries.items():
            measured, bias = state[a:a+2]
            result[a-self.offset] = (abs(reaction[i])/p.rated_field_reaction_pu-measured)/p.measurement_time_s
            target = np.clip(p.feedback_gain_pu*max(measured-p.current_limit_pu, 0.), 0., p.maximum_voltage_bias_pu)
            result[a+1-self.offset] = (target-bias)/p.bias_time_s
        return result

    def channel_names(self):
        return [p.generator_id+'.OEL.'+k for _, p in self.entries.values() for k in
                ('field_reaction_pu', 'field_current_pu', 'measured_current_pu', 'bias_pu', 'bias_state_pu', 'current_limit_pu')]

    def signals(self, state, current):
        if not self.entries:
            return {}
        reaction = self.reaction(state, current)
        result = {}
        for i, (a, p) in self.entries.items():
            values = dict(field_reaction_pu=reaction[i], field_current_pu=abs(reaction[i])/p.rated_field_reaction_pu,
                          measured_current_pu=state[a], bias_pu=np.clip(state[a+1], 0., p.maximum_voltage_bias_pu),
                          bias_state_pu=state[a+1], current_limit_pu=p.current_limit_pu)
            result.update({p.generator_id+'.OEL.'+k: float(v) for k, v in values.items()})
        return result

    def observe(self, states):
        for i, (a, p) in self.entries.items():
            v = states[a+1]
            self.overshoot[i] = max(self.overshoot.get(i, 0.), float(np.max(-v)), float(np.max(v-p.maximum_voltage_bias_pu)))

    def validate(self, channels, rtol, atol):
        limits = {}
        for i, (a, p) in self.entries.items():
            prefix = p.generator_id+'.OEL.'
            raw = np.asarray(channels[prefix+'bias_state_pu'])
            bounded = np.asarray(channels[prefix+'bias_pu'])
            tolerance = max(20*atol, 20*rtol*max(1., p.maximum_voltage_bias_pu))
            overshoot = max(self.overshoot.get(i, 0.), float(np.max(-raw)), float(np.max(raw-p.maximum_voltage_bias_pu)), 0.)
            limits[p.generator_id] = dict(within_limits=bool(np.isfinite(raw).all() and np.all(bounded>=0) and np.all(bounded<=p.maximum_voltage_bias_pu) and overshoot<=tolerance),
                                          maximum_raw_overshoot=overshoot, overshoot_tolerance=tolerance)
        return dict(ok=all(v['within_limits'] for v in limits.values()), limits=limits)
