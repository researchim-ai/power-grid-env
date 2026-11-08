"""Auditable synchronous CSV ingestion for the bounded dynamic motor profile.

No clock alignment, filtering, interpolation or inferred units are performed.
Three-phase inputs are instantaneous phase-to-neutral voltages / phase currents;
the amplitude-invariant Clarke convention preserves peak vector amplitudes.
"""
from __future__ import annotations

import csv
import io
import math
from html import escape
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from power_grid_env.dynamic_motor_identification import DynamicMotorIdentification, DynamicMotorTrace
from power_grid_env.study_artifacts import digest, encoded, read_json, safe_name, seal, verify
from power_grid_env.evidence import package_fingerprint

MAX_CSV_BYTES = 12_000_000
MAX_TOTAL_BYTES = 64_000_000


class Channel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    column: str = Field(min_length=1, max_length=128)
    gain: float = Field(default=1., gt=0, le=1e6)
    polarity: Literal[-1, 1] = 1
    offset: float = Field(default=0., ge=-1e6, le=1e6)
    raw_minimum: float | None = None
    raw_maximum: float | None = None

    @model_validator(mode='after')
    def rails(self):
        if (self.raw_minimum is None) != (self.raw_maximum is None):
            raise ValueError('both raw instrument rails must be declared together')
        if self.raw_minimum is not None and self.raw_minimum >= self.raw_maximum:
            raise ValueError('strictly ordered raw instrument rails required')
        return self


class Recording(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=64)
    source: str
    provenance: str = Field(min_length=1, max_length=1400)
    origin: Literal['synthetic', 'measured']
    split: Literal['fitting', 'validation']
    frequency_hz: float = Field(gt=0, le=400)
    coordinates: Literal['alpha_beta', 'abc_phase_to_neutral']
    voltage_unit: Literal['V', 'kV']
    current_unit: Literal['A', 'mA', 'kA']
    speed_unit: Literal['rad/s', 'rpm']
    time_unit: Literal['s', 'ms', 'us']
    time_column: str = Field(min_length=1, max_length=128)
    channels: dict[str, Channel]
    delimiter: Literal[',', ';', '\t'] = ','
    initial_stator_flux_vs: tuple[float, float]
    initial_rotor_flux_vs: tuple[float, float]
    current_sigma_a: float = Field(gt=0, le=1000)
    speed_sigma_rad_s: float = Field(gt=0, le=1000)
    maximum_zero_sequence_ratio: float = Field(default=.001, gt=0, le=.01)
    maximum_negative_sequence_ratio: float = Field(default=.01, gt=0, le=.05)

    @model_validator(mode='after')
    def geometry(self):
        safe_name(self.source)
        if not self.provenance.strip():
            raise ValueError('nonblank provenance required')
        expected = {'speed'} | ({'ua', 'ub', 'ia', 'ib'} if self.coordinates == 'alpha_beta' else {'u_a', 'u_b', 'u_c', 'i_a', 'i_b', 'i_c'})
        if set(self.channels) != expected:
            raise ValueError('explicit complete coordinate channel mapping required')
        names = [self.time_column] + [channel.column for channel in self.channels.values()]
        if len(set(names)) != len(names):
            raise ValueError('each mapped channel requires a distinct column')
        return self


class RecordingImport(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal['power-grid-env.motor-recordings/v1'] = 'power-grid-env.motor-recordings/v1'
    # All existing solver controls remain governed by its own strict schema.
    identification: dict
    recordings: list[Recording] = Field(min_length=2, max_length=8)

    @model_validator(mode='after')
    def protocol(self):
        if {'fitting', 'validation'} & self.identification.keys():
            raise ValueError('traces must come exclusively from declared CSV recordings')
        if len({r.id for r in self.recordings}) != len(self.recordings):
            raise ValueError('unique recording IDs required')
        for split in ('fitting', 'validation'):
            if not 1 <= sum(r.split == split for r in self.recordings) <= 4:
                raise ValueError('one to four recordings per split required')
        return self


def _csv(raw: bytes, recording: Recording):
    if not raw or len(raw) > MAX_CSV_BYTES:
        raise ValueError('CSV byte budget exceeded or empty source')
    try:
        rows = csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''), delimiter=recording.delimiter, strict=True)
        header = next(rows)
        if not header or len(header) > 64 or any(not h.strip() for h in header) or len(set(header)) != len(header):
            raise ValueError('unique nonblank CSV header required, at most 64 columns')
        required = [recording.time_column] + [c.column for c in recording.channels.values()]
        if not set(required) <= set(header):
            raise ValueError('mapped CSV column missing')
        indices = [header.index(c) for c in required]
        values = []
        for number, row in enumerate(rows, 2):
            if len(row) != len(header):
                raise ValueError(f'CSV row {number}: incomplete or excess fields')
            try:
                data = [float(row[i]) for i in indices]
            except ValueError:
                raise ValueError(f'CSV row {number}: mapped values must be numeric') from None
            if not all(math.isfinite(v) for v in data):
                raise ValueError(f'CSV row {number}: nonfinite mapped value')
            values.append(data)
            if len(values) > 20001:
                raise ValueError('CSV sample budget exceeded')
        if len(values) < 33:
            raise ValueError('at least 33 synchronous samples required')
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise ValueError('valid UTF-8 delimited CSV required') from exc
    data = np.asarray(values)
    for i, (name, channel) in enumerate(recording.channels.items()):
        if channel.raw_minimum is not None and (np.any(data[:, i + 1] <= channel.raw_minimum) or np.any(data[:, i + 1] >= channel.raw_maximum)):
            raise ValueError(f'{name}: raw instrument rail reached or exceeded; clipping cannot be excluded')
    time = data[:, 0] * {'s': 1., 'ms': .001, 'us': 1e-6}[recording.time_unit]
    origin = float(time[0])
    with np.errstate(over='ignore', invalid='ignore'):
        time = time - origin
    if not np.isfinite(time).all() or time[-1] > 2.:
        raise ValueError('finite recording duration at most two seconds required')
    if np.any(np.diff(time) <= 0):
        raise ValueError('strictly increasing shared clock required; duplicate timestamps forbidden')
    with np.errstate(over='ignore', invalid='ignore'):
        channels = {name: (data[:, i + 1] - c.offset) * c.gain * c.polarity for i, (name, c) in enumerate(recording.channels.items())}
    if any(not np.isfinite(values).all() for values in channels.values()):
        raise ValueError('finite calibrated channel values required')
    return time, channels, origin


def _sequence_ratio(time, vector, frequency):
    """Least-squares fundamental ±sequence with DC; works on nonuniform clocks."""
    angle = 2 * np.pi * frequency * time
    matrix = np.column_stack([np.exp(1j * angle), np.exp(-1j * angle), np.ones(len(time))])
    coefficients, _, rank, _ = np.linalg.lstsq(matrix, vector, rcond=None)
    if rank != 3 or abs(coefficients[0]) <= 1e-9:
        raise ValueError('resolved positive-sequence fundamental required')
    return float(abs(coefficients[1]) / abs(coefficients[0]))


def convert_recording(raw: bytes, recording: Recording):
    time, ch, origin = _csv(raw, recording)
    vu = {'V': 1., 'kV': 1000.}[recording.voltage_unit]
    iu = {'A': 1., 'mA': .001, 'kA': 1000.}[recording.current_unit]
    for name, values in ch.items():
        scale = vu if name.startswith('u') else iu if name.startswith('i') else 2 * math.pi / 60 if recording.speed_unit == 'rpm' else 1.
        limit = 2000. if name == 'speed' else 1e6
        if float(np.max(np.abs(values))) > limit / scale:
            raise ValueError(f'{name}: calibrated channel outside supported SI amplitude range')
    zero = {}
    if recording.coordinates == 'alpha_beta':
        u = vu * (ch['ua'] + 1j * ch['ub'])
        current = iu * (ch['ia'] + 1j * ch['ib'])
    else:
        def clarke(prefix, scale):
            phases = np.array([ch[f'{prefix}_{p}'] for p in 'abc']) * scale
            vector = 2 / 3 * (phases[0] - .5 * (phases[1] + phases[2]) + 1j * math.sqrt(3) / 2 * (phases[1] - phases[2]))
            zero[prefix] = float(np.max(np.abs(np.mean(phases, axis=0))) / max(float(np.max(np.abs(vector))), 1e-12))
            if zero[prefix] > recording.maximum_zero_sequence_ratio:
                raise ValueError(f'{prefix}: zero sequence exceeds balanced model profile')
            return vector
        u = clarke('u', vu)
        current = clarke('i', iu)
    if time[-1] * recording.frequency_hz < 3:
        raise ValueError('at least three electrical periods required for sequence screening')
    negative = _sequence_ratio(time, u, recording.frequency_hz)
    if negative > recording.maximum_negative_sequence_ratio:
        raise ValueError('voltage negative sequence exceeds balanced model profile')
    speed = ch['speed'] * (2 * math.pi / 60 if recording.speed_unit == 'rpm' else 1.)
    trace = DynamicMotorTrace(
        id=recording.id, provenance=f'{recording.origin}: {recording.provenance}; source SHA-256 {digest(raw)}',
        frequency_hz=recording.frequency_hz, time_s=time.tolist(), voltage_alpha_v=u.real.tolist(), voltage_beta_v=u.imag.tolist(),
        current_alpha_a=current.real.tolist(), current_beta_a=current.imag.tolist(), speed_rad_s=speed.tolist(),
        initial_stator_flux_vs=recording.initial_stator_flux_vs, initial_rotor_flux_vs=recording.initial_rotor_flux_vs,
        current_sigma_a=recording.current_sigma_a, speed_sigma_rad_s=recording.speed_sigma_rad_s)
    return trace, dict(id=recording.id, split=recording.split, origin=recording.origin, source_sha256=digest(raw),
                      samples=len(time), source_time_origin_s=origin, duration_s=float(time[-1]),
                      minimum_step_s=float(min(np.diff(time))), maximum_step_s=float(max(np.diff(time))),
                      zero_sequence_peak_ratios=zero, voltage_fundamental_negative_sequence_ratio=negative,
                      resampled=False, filtered=False, automatically_aligned=False)


def _prepare(protocol, sources):
    document = dict(protocol.identification, fitting=[], validation=[])
    diagnostics = []
    for recording, raw in zip(protocol.recordings, sources, strict=True):
        trace, diagnostic = convert_recording(raw, recording)
        document[recording.split].append(trace.model_dump(mode='json'))
        diagnostics.append(diagnostic)
    spec = DynamicMotorIdentification.model_validate(document)
    # Different CSV units, clocks or abc/αβ roundoff must not disguise reused
    # excitation as an independent experiment. This is a numerical duplicate
    # guard, not a statistical independence test or an arbitrary time-shift search.
    traces = spec.fitting + spec.validation
    for i, first in enumerate(traces):
        for second in traces[i + 1:]:
            if len(first.time_s) != len(second.time_s) or abs(first.frequency_hz - second.frequency_hz) > 1e-9:
                continue
            fields = ('time_s', 'voltage_alpha_v', 'voltage_beta_v', 'initial_stator_flux_vs', 'initial_rotor_flux_vs')
            if abs(first.speed_rad_s[0] - second.speed_rad_s[0]) <= 1e-9 and all(np.allclose(getattr(first, field), getattr(second, field), rtol=1e-10, atol=1e-9) for field in fields):
                raise ValueError('CSV recordings reuse the same excitation and initial state within conversion precision')
    return spec, dict(schema='power-grid-env.motor-recording-import-result/v1', prepared=True,
                      physical_solver_used=False, identification_approved=False,
                      source_authenticated=False, diagnostics=diagnostics)


def _html(spec, result):
    def plot(time, values, label):
        index = np.unique(np.linspace(0, len(time) - 1, min(500, len(time)), dtype=int))
        lo, hi = min(values), max(values)
        span = max(hi - lo, 1e-12)
        points = ' '.join(f'{40 + 580 * time[i] / time[-1]:.3f},{140 - 110 * (values[i] - lo) / span:.3f}' for i in index)
        return f'<svg viewBox="0 0 640 170" role="img" aria-label="{escape(label)}"><title>{escape(label)}</title><path d="M40 20V140H620" fill="none" stroke="#83928c"/><polyline points="{points}" fill="none" stroke="#235d52"/><text x="40" y="16">{escape(label)} [{lo:.5g}, {hi:.5g}]</text><text x="40" y="162">0 — {time[-1]:.5g} s</text></svg>'
    blocks = []
    diagnostics = {row['id']: row for row in result['diagnostics']}
    for trace in spec.fitting + spec.validation:
        row = diagnostics[trace.id]
        blocks.append('<section><h2>' + escape(trace.id) + ' / ' + escape(row['split']) + '</h2><p>' + escape(trace.provenance) + '</p><pre>' + escape(str(row)) + '</pre>' +
                      plot(trace.time_s, trace.voltage_alpha_v, 'uα, V instantaneous') +
                      plot(trace.time_s, trace.current_alpha_a, 'iα, A instantaneous') +
                      plot(trace.time_s, trace.speed_rad_s, 'ω, rad/s mechanical') + '</section>')
    return '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Подготовка записей двигателя</title><style>body{font:16px system-ui;max-width:1040px;margin:32px auto;padding:0 20px;color:#163c32}svg{width:100%;max-width:640px}text{font:12px system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px solid #b8c9c2;margin-top:32px}</style><h1>Записи подготовлены к идентификации</h1><p>Это проверка формата и преобразования, а не принятый паспорт двигателя. Физический подбор ещё не выполнялся. Единицы и калибровки заданы в протоколе. Фильтрация, пересэмплирование и автоматическое выравнивание не выполнялись. Проверка последовательностей — ограниченный скрининг, не доказательство симметрии всех переходных составляющих. SHA-256 подтверждает сохранность, а не подлинность источника.</p><p><a href="study.json">Вход решателя</a> · <a href="protocol.json">Протокол и калибровки</a> · <a href="result.json">Диагностика</a></p>' + ''.join(blocks) + '</html>'


def import_recordings(protocol_path, out):
    path = Path(protocol_path)
    protocol = RecordingImport.model_validate(read_json(path))
    root = path.parent.resolve()
    sources = []
    for recording in protocol.recordings:
        source = root / recording.source
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError('CSV source must be a regular file inside the protocol directory')
        with source.open('rb') as stream:
            raw = stream.read(MAX_CSV_BYTES + 1)
        sources.append(raw)
        if len(raw) > MAX_CSV_BYTES or sum(map(len, sources)) > MAX_TOTAL_BYTES:
            raise ValueError('CSV source byte budget exceeded')
    code = package_fingerprint()
    spec, result = _prepare(protocol, sources)
    result['producer_sha256'] = code
    if package_fingerprint() != code:
        raise ValueError('recording importer changed during conversion')
    out = Path(out)
    if out.exists():
        raise ValueError('new output directory required')
    out.mkdir(parents=True)
    (out / 'sources').mkdir()
    for i, raw in enumerate(sources):
        (out / 'sources' / f'{i:02d}.csv').write_bytes(raw)
    (out / 'protocol.json').write_bytes(encoded(protocol.model_dump(mode='json')))
    (out / 'study.json').write_bytes(encoded(spec.model_dump(mode='json')))
    (out / 'result.json').write_bytes(encoded(result))
    (out / 'report.html').write_text(_html(spec, result), encoding='utf-8')
    seal(out)
    return result


def verify_recordings(out):
    """Recompute conversion from retained source bytes, beyond inventory hashes."""
    root = Path(out)
    identity = verify(root)
    protocol = RecordingImport.model_validate(read_json(root / 'protocol.json'))
    sources = []
    for i in range(len(protocol.recordings)):
        with (root / 'sources' / f'{i:02d}.csv').open('rb') as stream:
            sources.append(stream.read(MAX_CSV_BYTES + 1))
    if any(len(raw) > MAX_CSV_BYTES for raw in sources) or sum(map(len, sources)) > MAX_TOTAL_BYTES:
        raise ValueError('CSV source byte budget exceeded')
    spec, result = _prepare(protocol, sources)
    actual = read_json(root / 'result.json')
    result['producer_sha256'] = actual.get('producer_sha256')
    if not isinstance(result['producer_sha256'], str) or len(result['producer_sha256']) != 64 or any(c not in '0123456789abcdef' for c in result['producer_sha256']):
        raise ValueError('producer identity missing')
    saved = DynamicMotorIdentification.model_validate(read_json(root / 'study.json'))
    if actual != result or saved.model_dump(mode='json') != spec.model_dump(mode='json') or (root / 'report.html').read_text(encoding='utf-8') != _html(spec, result):
        raise ValueError('recording conversion or diagnostics inconsistent with retained source')
    return dict(verified=True, manifest_sha256=identity, recordings=len(sources), source_authenticated=False)


def load_recordings(out):
    """Load a solver document only from a semantically consistent CSV package."""
    root = Path(out)
    identity = verify_recordings(root)['manifest_sha256']
    document = read_json(root / 'study.json')
    if verify(root) != identity:
        raise ValueError('CSV package changed while loading solver input')
    return document
