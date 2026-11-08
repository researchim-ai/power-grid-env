"""Deterministic raw-observation residual audit, not statistical inference.

Optional training-speed smoothing never changes the held-out observations or
the raw data used for final acceptance. Sensor offsets are diagnosed, not fitted.
"""
from html import escape
import time
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy.signal import savgol_filter

from power_grid_env.dynamic_motor_identification import DynamicMotorIdentification, WorkBudget, identify_dynamic, replay
from power_grid_env.evidence import package_fingerprint
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.study_artifacts import digest, encoded, seal


class ResidualLimits(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    maximum_mean_sigma: float = Field(default=.5, gt=0, le=5)
    maximum_rms_sigma: float = Field(default=1.5, gt=0, le=5)
    maximum_half_window_drift_sigma: float = Field(default=1., gt=0, le=5)


class DynamicIdentificationAudit(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal['power-grid-env.dynamic-identification-audit/v1'] = 'power-grid-env.dynamic-identification-audit/v1'
    identification: DynamicMotorIdentification
    training_speed_filter_window: Literal[0, 5, 9, 17] = 0
    residual_limits: ResidualLimits = Field(default_factory=ResidualLimits)


def residual_metrics(error):
    error = np.asarray(error, dtype=float)
    if error.ndim != 1 or len(error) < 4 or not np.isfinite(error).all():
        raise ValueError('finite one-dimensional normalized residual required')
    half = len(error) // 2
    scale = max(float(np.max(np.abs(error))), np.finfo(float).tiny)
    normalized = error / scale
    with np.errstate(over='ignore', invalid='ignore'):
        metrics = dict(maximum_sigma=float(np.max(np.abs(error))),
                       mean_sigma=float(np.mean(normalized) * scale), rms_sigma=float(np.sqrt(np.mean(normalized**2)) * scale),
                       half_window_drift_sigma=float((np.mean(normalized[half:]) - np.mean(normalized[:half])) * scale))
    if not all(np.isfinite(value) for value in metrics.values()):
        raise ValueError('residual summary outside finite numerical range')
    return metrics


def assess_metrics(metrics, limits, maximum_sigma):
    return bool(metrics['maximum_sigma'] <= maximum_sigma and
                abs(metrics['mean_sigma']) <= limits.maximum_mean_sigma and
                metrics['rms_sigma'] <= limits.maximum_rms_sigma and
                abs(metrics['half_window_drift_sigma']) <= limits.maximum_half_window_drift_sigma)


def training_document(request):
    """Return a fresh fit-only copy; the validation split remains byte-identical."""
    spec = request.identification.model_copy(deep=True)
    window = request.training_speed_filter_window
    if window:
        for trace in spec.fitting:
            # Savitzky-Golay assumes uniform spacing. Reject instead of silently
            # resampling a nonuniform acquisition clock.
            steps = np.diff(trace.time_s)
            if not np.allclose(steps, steps[0], rtol=1e-7, atol=1e-12):
                raise ValueError('training speed filter requires a uniform clock')
            values = savgol_filter(trace.speed_rad_s, window, 2, mode='interp')
            # The observed initial speed is the ODE initial condition. Filtering
            # must not quietly change that physical initial state.
            values[0] = trace.speed_rad_s[0]
            trace.speed_rad_s = values.tolist()
            trace.provenance += f'; explicit training-only quadratic Savitzky-Golay window={window}; initial observation preserved'
    return DynamicMotorIdentification.model_validate(spec.model_dump(mode='json'))


def audit_identification(document):
    request = DynamicIdentificationAudit.model_validate(document)
    raw = request.identification
    code = package_fingerprint()
    budget = WorkBudget(raw)
    prepared = training_document(request)
    result = identify_dynamic(prepared)
    result['fit_passed'] = bool(result.get('passed'))
    result['fit_dataset_sha256'] = result['dataset_sha256']
    result['raw_identification_sha256'] = digest(encoded(raw.model_dump(mode='json')))
    result['audit_protocol_sha256'] = digest(encoded(request.model_dump(mode='json')))
    result['residual_limits'] = request.residual_limits.model_dump(mode='json')
    result['preprocessing'] = dict(training_speed_filter_window=request.training_speed_filter_window,
                                   polynomial_order=2 if request.training_speed_filter_window else None,
                                   uniform_clock_required=bool(request.training_speed_filter_window),
                                   initial_speed_preserved=True, validation_filtered=False, raw_acceptance=True)
    result['sensor_bias_estimated'] = False
    result['statistical_confidence_claimed'] = False
    result['raw_residuals_passed'] = False
    if not result.get('completed'):
        return result
    budget.rhs_calls = result['rhs_evaluations']
    budget.evaluations = result['evaluations']
    try:
        passport = FluxMachinePassport.model_validate(result['passport'])
        assessments = []
        trajectories = {}
        for split, traces in [('fitting', raw.fitting), ('validation', raw.validation)]:
            for trace in traces:
                response = replay(passport, trace, budget, conditional=False)
                channels = dict(current_alpha=(response['current'].real - trace.current_alpha_a) / trace.current_sigma_a,
                                current_beta=(response['current'].imag - trace.current_beta_a) / trace.current_sigma_a,
                                speed=(response['speed'] - trace.speed_rad_s) / trace.speed_sigma_rad_s)
                metrics = {key: residual_metrics(values) for key, values in channels.items()}
                passed = all(assess_metrics(values, request.residual_limits, raw.tolerance_sigma) for values in metrics.values())
                assessments.append(dict(id=trace.id, split=split, passed=passed, residual_metrics=metrics,
                                        current_error_sigma=max(metrics['current_alpha']['maximum_sigma'], metrics['current_beta']['maximum_sigma']),
                                        speed_error_sigma=metrics['speed']['maximum_sigma']))
                trajectories[trace.id] = dict(time_s=trace.time_s, current_alpha_a=response['current'].real.tolist(),
                                              current_beta_a=response['current'].imag.tolist(), speed_rad_s=response['speed'].tolist())
        result['fit_assessments'] = result['assessments']
        result['assessments'] = assessments
        result['trajectories'] = trajectories
        result['raw_residuals_passed'] = all(row['passed'] for row in assessments)
        result['passed'] = bool(result['fit_passed'] and result['raw_residuals_passed'])
        if result['fit_passed'] and not result['passed']:
            result['reason'] = 'raw_residual_quality_rejected'
        result['passport']['provenance']['residual_audit'] = 'Raw-observation deterministic residual audit ' + result['audit_protocol_sha256']
        if code != package_fingerprint():
            raise ValueError('audit producer changed')
    except (ValueError, ArithmeticError, TimeoutError, np.linalg.LinAlgError) as exc:
        result.update(completed=False, passed=False, raw_residuals_passed=False,
                      status='runtime_limit_exceeded' if isinstance(exc, TimeoutError) else 'audit_failed', reason=str(exc))
    result.update(rhs_evaluations=budget.rhs_calls, elapsed_s=time.monotonic() - budget.started)
    return result


def audit_html(request, result):
    from power_grid_env.advanced_studies import dynamic_identification_html
    base = dynamic_identification_html(request.identification, result)
    detail = '<h2>Контроль исходных наблюдений</h2><p>Проверка максимума, RMS, среднего и дрейфа между половинами окна в заданных sigma. Это детерминированные инженерные пределы, а не статистические доверительные интервалы. Проверочные данные не фильтруются. Все итоговые невязки вычислены относительно исходных наблюдений. Смещения датчиков не подбираются.</p><pre>' + escape(str(dict(preprocessing=result['preprocessing'], limits=result['residual_limits'], fit_passed=result['fit_passed'], raw_residuals_passed=result['raw_residuals_passed'], assessments=result.get('assessments')))) + '</pre>'
    return base + detail


def run_audit_artifact(document, out):
    from pathlib import Path
    request = DynamicIdentificationAudit.model_validate(document)
    out = Path(out)
    if out.exists():
        raise ValueError('new audit output directory required')
    code = package_fingerprint()
    result = audit_identification(request)
    if package_fingerprint() != code:
        raise ValueError('audit producer changed')
    out.mkdir(parents=True)
    (out / 'study.json').write_bytes(encoded(request.model_dump(mode='json')))
    (out / 'fit-input.json').write_bytes(encoded(training_document(request).model_dump(mode='json')))
    (out / 'result.json').write_bytes(encoded(result))
    (out / 'report.html').write_text(audit_html(request, result), encoding='utf-8')
    if result.get('passed'):
        (out / 'passport.json').write_bytes(encoded(result['passport']))
    seal(out)
    return result
