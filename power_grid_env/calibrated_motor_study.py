"""Known-calibration CSV -> raw-observation audit. No joint gain estimation.

Evidence labels are declarations, not authenticated calibration certificates.
Unknown common voltage/current scales have a physical gauge freedom with R/L/J
and cannot be resolved merely by a good fit or a separate excitation.
"""
from pathlib import Path
from typing import Literal
from html import escape

from pydantic import BaseModel, ConfigDict, Field

from power_grid_env.dynamic_identification_audit import DynamicIdentificationAudit, ResidualLimits, run_audit_artifact
from power_grid_env.motor_recordings import RecordingImport, import_recordings, load_recordings
from power_grid_env.study_artifacts import encoded, read_json, safe_name, seal, digest
from power_grid_env.evidence import package_fingerprint


class CalibrationReference(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    basis: Literal['synthetic_known', 'declared_external_calibration']
    evidence: str = Field(min_length=1, max_length=2048, pattern=r'\S')


class CalibratedMotorStudy(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal['power-grid-env.calibrated-motor-study/v1'] = 'power-grid-env.calibrated-motor-study/v1'
    recording_protocol: str
    voltage_reference: CalibrationReference
    current_reference: CalibrationReference
    speed_reference: CalibrationReference
    joint_sensor_parameter_estimation: Literal[False] = False
    initial_flux_reference: Literal['known_in_declared_alpha_beta_axes']
    training_speed_filter_window: Literal[0, 5, 9, 17] = 0
    residual_limits: ResidualLimits = Field(default_factory=ResidualLimits)


def run_calibrated_study(protocol_path, out):
    path = Path(protocol_path)
    request = CalibratedMotorStudy.model_validate(read_json(path))
    safe_name(request.recording_protocol)
    source = path.parent / request.recording_protocol
    if source.is_symlink() or not source.resolve().is_relative_to(path.parent.resolve()):
        raise ValueError('recording protocol must remain inside study directory')
    recordings = RecordingImport.model_validate(read_json(source))
    references = [request.voltage_reference, request.current_reference, request.speed_reference]
    if any(trace.origin == 'measured' for trace in recordings.recordings) and any(ref.basis == 'synthetic_known' for ref in references):
        raise ValueError('measured recordings require declared external references for every quantity')
    # Validate the downstream settings before creating artifacts. Unknown fluxes
    # and jointly estimated sensor scales are deliberately unsupported.
    out = Path(out)
    if out.exists(): raise ValueError('new calibrated-study directory required')
    code = package_fingerprint()
    out.mkdir(parents=True)
    prepared = out / 'recordings'
    import_recordings(source, prepared)
    raw = load_recordings(prepared)
    audit = DynamicIdentificationAudit(identification=raw, training_speed_filter_window=request.training_speed_filter_window,
                                       residual_limits=request.residual_limits)
    result = run_audit_artifact(audit.model_dump(mode='json'), out / 'identification')
    summary = dict(schema='power-grid-env.calibrated-motor-study-result/v1', completed=result['completed'], passed=result['passed'],
                   reason=result.get('reason'), calibration_assumed_known=True, calibration_authenticated=False,
                   sensor_scales_estimated=False, initial_fluxes_estimated=False, producer_sha256=code,
                   protocol_sha256=digest(encoded(request.model_dump(mode='json'))),
                   prepared_manifest_sha256=digest((prepared / 'manifest.json').read_bytes()),
                   audit_protocol_sha256=result['audit_protocol_sha256'],
                   field_validated=False, statistical_confidence_claimed=False)
    if code != package_fingerprint(): raise ValueError('calibrated study producer changed')
    (out / 'study.json').write_bytes(encoded(request.model_dump(mode='json')))
    (out / 'result.json').write_bytes(encoded(summary))
    if result.get('passed'):
        passport = dict(result['passport'])
        passport['provenance'] = dict(passport['provenance'], measurement_contract='Declared known calibration protocol ' + summary['protocol_sha256'])
        (out / 'passport.json').write_bytes(encoded(passport))
    report = '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Калиброванные записи двигателя</title><style>body{font:16px system-ui;max-width:900px;margin:32px auto;padding:0 20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Калиброванные записи двигателя</h1><p>Принят: ' + escape(str(summary['passed'])) + '. ' + escape(str(summary['reason'])) + '</p><p>Калибровки считаются известными по явному заявлению автора. Их подлинность не проверена. Совместный подбор масштабов датчиков и параметров двигателя не выполняется: хороший fit не устраняет неопределённость абсолютных масштабов.</p><p><a href="recordings/report.html">Исходные каналы и преобразование</a> · <a href="identification/report.html">Физический подбор и raw-невязки</a> · <a href="study.json">Заявленные опоры калибровки</a> · <a href="result.json">Итог JSON</a></p><pre>' + escape(str(request.model_dump(mode='json'))) + '</pre></html>'
    (out / 'report.html').write_text(report, encoding='utf-8')
    seal(out)
    return summary
