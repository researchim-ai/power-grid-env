"""Balanced radial preliminary sizing, with explicit owner-supplied ratings.

No normative tables are inferred. Faults are steady symmetrical three-phase
faults under a stiff upstream source, not IEC 60909 or breaker coordination.
"""
import math
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from power_grid_env.study_artifacts import encoded, digest, read_json, seal, verify

class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Source(Contract):
    reference: str = Field(min_length=1, max_length=4096)
    conditions: str = Field(min_length=1, max_length=4096)
    @model_validator(mode='after')
    def nonblank(self):
        if not self.reference.strip() or not self.conditions.strip():
            raise ValueError('nonblank provenance and applicability required')
        return self

class Cable(Contract):
    id: str = Field(min_length=1, max_length=64)
    source: Source
    area_mm2: float = Field(gt=0)
    ampacity_a: float = Field(gt=0)
    r_operating_ohm_km: float = Field(gt=0)
    x_ohm_km: float = Field(ge=0)
    k_a_sqrt_s_mm2: float = Field(gt=0)
    rated_voltage_v: float = Field(gt=0)

class Transformer(Contract):
    id: str = Field(min_length=1, max_length=64)
    source: Source
    secondary_v: float = Field(gt=0)
    rating_kva: float = Field(gt=0)
    impedance_percent: float = Field(gt=0, le=100)
    resistance_percent: float = Field(gt=0, le=100)
    short_time_a: float = Field(gt=0)
    short_time_s: float = Field(gt=0, le=5)
    @model_validator(mode='after')
    def impedance(self):
        if self.resistance_percent > self.impedance_percent:
            raise ValueError('transformer resistance exceeds impedance magnitude')
        return self

class Installation(Contract):
    source: Source
    method: str = Field(min_length=1)
    ambient_c: float
    temperature_factor: float = Field(gt=0, le=1)
    grouping_factor: float = Field(gt=0, le=1)
    other_factor: float = Field(gt=0, le=1)
    length_m: float = Field(gt=0)
    clearing_s: float = Field(gt=0, le=5)
    breaker_interrupting_a: float = Field(gt=0)

class SizingTask(Contract):
    schema_version: Literal['power-grid-env.electrical-sizing/v1'] = 'power-grid-env.electrical-sizing/v1'
    id: str = Field(min_length=1,max_length=64)
    voltage_v: float = Field(gt=0, le=1000)
    demand_kva: float = Field(gt=0)
    lagging_power_factor: float = Field(gt=0, le=1)
    max_drop_percent: float = Field(gt=0, le=20)
    installation: Installation
    cables: list[Cable] = Field(min_length=1,max_length=128)
    transformers: list[Transformer] = Field(min_length=1,max_length=128)
    @model_validator(mode='after')
    def catalog(self):
        for entries in (self.cables,self.transformers):
            if len({e.id for e in entries}) != len(entries):
                raise ValueError('duplicate catalog id')
        if any(t.secondary_v != self.voltage_v for t in self.transformers):
            raise ValueError('catalog secondary voltage differs from task')
        if not self.installation.method.strip():
            raise ValueError('installation method required')
        return self

class Selection(Contract):
    cable_id: str
    transformer_id: str


def calculate(task, selection):
    t=SizingTask.model_validate(task)
    p=Selection.model_validate(selection)
    c=next((c for c in t.cables if c.id==p.cable_id),None)
    x=next((x for x in t.transformers if x.id==p.transformer_id),None)
    if c is None or x is None:
        raise ValueError('selection must belong to owner catalog')
    a=t.installation
    current=t.demand_kva*1000/(math.sqrt(3)*t.voltage_v)
    ampacity=c.ampacity_a*a.temperature_factor*a.grouping_factor*a.other_factor
    r=c.r_operating_ohm_km*a.length_m/1000
    reactance=c.x_ohm_km*a.length_m/1000
    zbase=t.voltage_v**2/(x.rating_kva*1000)
    rt=zbase*x.resistance_percent/100
    xt=zbase*math.sqrt(x.impedance_percent**2-x.resistance_percent**2)/100
    bus_fault=t.voltage_v/(math.sqrt(3)*math.hypot(rt,xt))
    end_fault=t.voltage_v/(math.sqrt(3)*math.hypot(rt+r,xt+reactance))
    drop=math.sqrt(3)*current*(r*t.lagging_power_factor+reactance*math.sqrt(1-t.lagging_power_factor**2))
    # Upstream terminal cable fault is the worst constant RMS duty in this
    # passive radial profile; downstream-only current would understate it.
    cable_i2t=bus_fault**2*a.clearing_s
    cable_limit=(c.k_a_sqrt_s_mm2*c.area_mm2)**2
    checks=dict(cable_voltage=c.rated_voltage_v>=t.voltage_v,
                ampacity=current<=ampacity,voltage_drop=100*drop/t.voltage_v<=t.max_drop_percent,
                transformer_loading=t.demand_kva<=x.rating_kva,
                cable_adiabatic=cable_i2t<=cable_limit,
                breaker_interrupting=bus_fault<=a.breaker_interrupting_a,
                transformer_short_time=a.clearing_s<=x.short_time_s and bus_fault<=x.short_time_a)
    result=dict(schema='power-grid-env.electrical-sizing-result/v1',
                task_sha256=digest(encoded(t.model_dump(mode='json'))),
                selection=p.model_dump(mode='json'),
                values=dict(load_current_a=current,derated_ampacity_a=ampacity,
                            cable_drop_percent=100*drop/t.voltage_v,
                            transformer_loading_pu=t.demand_kva/x.rating_kva,
                            bus_fault_a=bus_fault,end_fault_a=end_fault,
                            cable_fault_i2t_a2s=cable_i2t,cable_limit_i2t_a2s=cable_limit,
                            cable_r_ohm=r,cable_x_ohm=reactance),
                checks=checks,passes_declared_checks=all(checks.values()),
                normative_approval=False,
                scope='balanced LV single radial feeder; supplied operating resistance and derating; linear cable-only voltage drop; stiff source symmetrical RMS fault; fixed clearing time; no motor contribution, peak making duty, earth faults, protection coordination or dynamic acceptance')
    encoded(result)  # Reject overflow/nonfinite derived values before publishing.
    return result


def run_sizing(task,selection,out):
    t=SizingTask.model_validate(task).model_dump(mode='json')
    p=Selection.model_validate(selection).model_dump(mode='json')
    result=calculate(t,p)
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for name,value in [('task',t),('selection',p),('result',result)]:
        (root/f'{name}.json').write_bytes(encoded(value))
    seal(root)
    return result


def verify_sizing(root,*,expected_task=None,expected_selection=None):
    root=Path(root);before=verify(root)
    t=SizingTask.model_validate(read_json(root/'task.json')).model_dump(mode='json')
    p=Selection.model_validate(read_json(root/'selection.json')).model_dump(mode='json')
    if expected_task is not None and t!=SizingTask.model_validate(expected_task).model_dump(mode='json'):
        raise ValueError('owner task mismatch')
    if expected_selection is not None and p!=Selection.model_validate(expected_selection).model_dump(mode='json'):
        raise ValueError('owner selection mismatch')
    result=calculate(t,p)
    if read_json(root/'result.json')!=result:raise ValueError('sizing result mismatch')
    if verify(root)!=before:raise ValueError('artifact changed during verification')
    return dict(verified=True,manifest_sha256=before,**result)
