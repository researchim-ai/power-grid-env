"""Conditional motor identification with an explicit independent DC R_s anchor.

The interval is deterministic and conditional on declared instrument bounds,
connection and equal temperature. It is not a statistical confidence interval.
Only the nominal passport is approved, after all three conditional raw audits.
"""
from pathlib import Path
from typing import Literal
from html import escape
import time
import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from power_grid_env.calibrated_motor_study import CalibrationReference
from power_grid_env.dynamic_identification_audit import DynamicIdentificationAudit, audit_identification, audit_html, training_document
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded, digest, seal


class DCPoint(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    voltage_v: float = Field(gt=0, le=1e6)
    current_a: float = Field(gt=0, le=1e6)
    voltage_absolute_error_v: float = Field(ge=0, le=1000)
    current_absolute_error_a: float = Field(ge=0, le=1000)

    @model_validator(mode='after')
    def resolved(self):
        if self.voltage_v <= self.voltage_absolute_error_v or self.current_a <= self.current_absolute_error_a:
            raise ValueError('DC voltage and current must be resolved above their absolute error bounds')
        return self


class StatorResistanceMeasurement(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    origin: Literal['synthetic', 'measured']
    provenance: str = Field(min_length=1, max_length=2048, pattern=r'\S')
    connection: Literal['phase_to_neutral', 'wye_line_to_line']
    voltage_is_winding_only: Literal[True]
    settled_dc_confirmed: Literal[True]
    measurement_temperature_c: float = Field(ge=-50, le=200)
    trace_temperature_c: float = Field(ge=-50, le=200)
    voltage_reference: CalibrationReference
    current_reference: CalibrationReference
    points: list[DCPoint] = Field(min_length=2, max_length=64)
    maximum_relative_interval_width: float = Field(default=.0001, gt=0, le=.01)

    @model_validator(mode='after')
    def physical(self):
        if self.measurement_temperature_c != self.trace_temperature_c:
            raise ValueError('DC anchor and dynamic traces require the same declared temperature; correction is unsupported')
        if len({point.current_a for point in self.points}) < 2:
            raise ValueError('at least two distinct resolved DC current levels required')
        if self.origin == 'measured' and any(ref.basis != 'declared_external_calibration' for ref in [self.voltage_reference, self.current_reference]):
            raise ValueError('measured DC anchor requires external voltage/current calibration references')
        return self


def resistance_interval(measurement):
    measurement = StatorResistanceMeasurement.model_validate(measurement)
    factor = 2. if measurement.connection == 'wye_line_to_line' else 1.
    intervals = []
    for point in measurement.points:
        low = (point.voltage_v-point.voltage_absolute_error_v)/(point.current_a+point.current_absolute_error_a)/factor
        high = (point.voltage_v+point.voltage_absolute_error_v)/(point.current_a-point.current_absolute_error_a)/factor
        if not math.isfinite(low) or not math.isfinite(high) or low<=0 or high<=0: raise ValueError('DC resistance bounds outside positive finite numerical range')
        intervals.append([low,high])
    low=max(row[0] for row in intervals); high=min(row[1] for row in intervals)
    if low>high: raise ValueError('DC observations have no common resistance within declared error bounds')
    nominal=low+(high-low)/2
    relative=(high-low)/nominal
    if relative>measurement.maximum_relative_interval_width:
        raise ValueError('DC resistance interval exceeds the declared resolution requirement')
    return dict(lower_ohm=low,nominal_ohm=nominal,upper_ohm=high,relative_interval_width=relative,
                point_intervals_ohm=intervals,connection_factor=factor,source_sha256=digest(encoded(measurement.model_dump(mode='json'))),
                statistical_confidence_claimed=False,calibration_authenticated=False)


class AnchoredDynamicIdentification(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal['power-grid-env.anchored-dynamic-identification/v1']='power-grid-env.anchored-dynamic-identification/v1'
    audit: DynamicIdentificationAudit
    stator_resistance_measurement: StatorResistanceMeasurement
    maximum_total_rhs_evaluations: int = Field(default=10000000,ge=1,le=30000000)
    maximum_total_wall_s: float = Field(default=360.,gt=0,le=1800.)

    @model_validator(mode='after')
    def supported(self):
        remaining=[name for name in self.audit.identification.electrical_parameters if name!='stator_r_ohm']
        if not remaining: raise ValueError('DC anchor must leave at least one electrical parameter to estimate')
        resistance_interval(self.stator_resistance_measurement)
        return self


def anchored_audit(request, resistance):
    """Only R_s/its provenance and the list of unknowns change; observations stay raw."""
    audit=request.audit.model_copy(deep=True)
    audit.identification.initial.stator_r_ohm=resistance
    audit.identification.initial.provenance['stator_resistance_anchor']='Independent declared DC anchor '+digest(encoded(request.stator_resistance_measurement.model_dump(mode='json')))
    audit.identification.electrical_parameters=[name for name in audit.identification.electrical_parameters if name!='stator_r_ohm']
    return DynamicIdentificationAudit.model_validate(audit.model_dump(mode='json'))


def run_anchored_identification(document,out):
    request=AnchoredDynamicIdentification.model_validate(document)
    interval=resistance_interval(request.stator_resistance_measurement)
    out=Path(out)
    if out.exists(): raise ValueError('new anchored-identification directory required')
    code=package_fingerprint(); started=time.monotonic(); used=0; rows=[]; results={}
    out.mkdir(parents=True)
    (out/'study.json').write_bytes(encoded(request.model_dump(mode='json')))
    (out/'dc-anchor.json').write_bytes(encoded(interval))
    for name in ('nominal','lower','upper'):
        remaining=request.maximum_total_rhs_evaluations-used
        wall=request.maximum_total_wall_s-(time.monotonic()-started)
        if remaining<=0 or wall<=0: break
        audit=anchored_audit(request,interval[name+'_ohm'])
        audit.identification.maximum_rhs_evaluations=min(audit.identification.maximum_rhs_evaluations,remaining)
        audit.identification.maximum_wall_s=min(audit.identification.maximum_wall_s,wall)
        result=audit_identification(audit)
        used+=result['rhs_evaluations']; results[name]=result
        row=dict(id=name,resistance_ohm=interval[name+'_ohm'],completed=result['completed'],passed=result['passed'],
                 identifiable=bool(result.get('identifiable')),rhs_evaluations=result['rhs_evaluations'],reason=result.get('reason'))
        rows.append(row)
        path=out/name;path.mkdir()
        (path/'study.json').write_bytes(encoded(audit.model_dump(mode='json')))
        (path/'fit-input.json').write_bytes(encoded(training_document(audit).model_dump(mode='json')))
        (path/'result.json').write_bytes(encoded(result))
        (path/'report.html').write_text(audit_html(audit,result),encoding='utf-8')
        # Conditional candidates remain in result.json; no endpoint publishes
        # an independent approved passport before the whole contract succeeds.
        seal(path)
        if not result['completed'] or not result['passed']: break
    bracket_completed=len(rows)==3 and all(row['completed'] for row in rows)
    rejection_witness=any(row['completed'] and not row['passed'] for row in rows)
    completed=bracket_completed or rejection_witness
    within_budget=used<=request.maximum_total_rhs_evaluations and time.monotonic()-started<=request.maximum_total_wall_s
    passed=bracket_completed and all(row['passed'] for row in rows) and within_budget
    summary=dict(schema='power-grid-env.anchored-dynamic-identification-result/v1',completed=completed,passed=passed,
                 identifiable=bool(completed and all(row['identifiable'] for row in rows)),
                 reason=None if passed else 'anchor_work_incomplete' if not completed else 'anchor_total_work_limit_exceeded' if not within_budget else 'anchor_bracket_raw_replay_rejected',
                 anchor=interval,cases=rows,rhs_evaluations=used,elapsed_s=time.monotonic()-started,
                 bracket_completed=bracket_completed,skipped_cases=[name for name in ('nominal','lower','upper') if name not in results],
                 additional_dc_measurement_required=True,original_observations_changed=False,
                 fitted_electrical_parameters=[n for n in request.audit.identification.electrical_parameters if n!='stator_r_ohm'],
                 source_protocol_sha256=digest(encoded(request.model_dump(mode='json'))),code_sha256=code,
                 continuous_interval_verified=False,total_work_budget_satisfied=within_budget,
                 field_validated=False,statistical_confidence_claimed=False,calibration_authenticated=False)
    if code!=package_fingerprint():raise ValueError('anchored identification producer changed')
    if passed:
        passport=results['nominal']['passport']
        passport['provenance']['stator_resistance_anchor']='Declared independent DC interval '+interval['source_sha256']
        (out/'passport.json').write_bytes(encoded(passport))
    (out/'result.json').write_bytes(encoded(summary))
    report='<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>DC-опора сопротивления статора</title><style>body{font:16px system-ui;max-width:1000px;margin:32px auto;padding:0 20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Независимая DC-опора Rs</h1><p>Принят: '+escape(str(passed))+'. '+escape(str(summary['reason']))+'</p><p>Требуются дополнительные независимые U/I данные при той же температуре. Интервал условен на заявленных границах погрешностей и подключении; это не статистический доверительный интервал. Проверки концов и номинала не доказывают выполнение всех критериев на непрерывном интервале или независимую точность потерь сети. Калибровки не аутентифицированы.</p><h2>Исходные DC-наблюдения</h2><pre>'+escape(str(request.stator_resistance_measurement.model_dump(mode='json')))+'</pre><h2>Пересечение допустимых сопротивлений</h2><pre>'+escape(str(interval))+'</pre><ul>'+''.join('<li><a href="'+row['id']+'/report.html">'+escape(row['id'])+'</a>: '+escape(str(row['passed']))+'</li>' for row in rows)+'</ul><p><a href="study.json">Исходный протокол, опора и наблюдения</a> · <a href="result.json">Все критерии</a></p></html>'
    (out/'report.html').write_text(report,encoding='utf-8');seal(out)
    return summary
