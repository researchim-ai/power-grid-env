"""Reproducible eligibility audit; never silently repairs publisher speed units."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import numpy as np

ARCHIVE_SHA256 = '078a9468363c478590524b4fe61b0d8672993f92c0c4e99939a8501fca729cca'
URL = 'https://forge.lias-lab.fr/datasets/mcsadc/mcsadc-motor-rotorbarfailure-1_2023.tar.gz'
REVISION = '23e893197118b376190d0459d42f145e8d2d9b82'
SELECTED = {'ccs0.csv': (1432, 1439), 'ccs20.csv': (1458, 1468), 'ccs40.csv': (1490, 1492)}


def inspect_record(content, rpm_range):
    a = np.loadtxt(io.BytesIO(content), delimiter=',')
    if a.ndim != 2 or a.shape[1] != 9 or len(a) < 10 or not np.isfinite(a).all():
        raise ValueError('expected finite nine-channel record')
    dt = np.diff(a[:, 0])
    if np.any(dt <= 0):
        raise ValueError('non-monotonic clock')
    rpm = float(np.mean(a[:, 7]) * 60 / (2 * np.pi))
    return dict(rows=len(a), duration_s=float(a[-1, 0]-a[0, 0]),
                sample_interval_s=float(np.median(dt)),
                phase_current_rms_a=np.sqrt(np.mean(a[:, 1:4]**2, axis=0)).tolist(),
                phase_voltage_rms_v=np.sqrt(np.mean(a[:, 4:7]**2, axis=0)).tolist(),
                speed_raw_min_max=[float(a[:, 7].min()), float(a[:, 7].max())],
                rpm_if_documented_rad_s=rpm, publisher_rpm_range=list(rpm_range),
                speed_matches_documented_units=bool(rpm_range[0] <= rpm <= rpm_range[1]),
                speed_factor_applied=1., sha256=hashlib.sha256(content).hexdigest())


def audit(archive, out):
    archive, out = Path(archive), Path(out)
    if archive.stat().st_size != 50372333 or hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('pinned LIAS archive integrity failed')
    rows = {}
    with tarfile.open(archive) as t:
        members = t.getmembers()
        for name, rpm in SELECTED.items():
            matches = [m for m in members if m.isfile() and m.name.split('/')[-1] == name]
            if len(matches) != 1 or matches[0].size > 2_000_000:
                raise ValueError('unexpected selected member')
            content = t.extractfile(matches[0]).read()
            rows[name] = inspect_record(content, rpm)
            target = out/'samples'/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    result = dict(source_url=URL, repository='https://github.com/lias-laboratory/mcsa-dc',
                  repository_revision=REVISION, archive_sha256=ARCHIVE_SHA256,
                  license='CC-BY-4.0', records=rows, speed_units_resolved=False,
                  measured_startup_qualified=False, field_validated=False,
                  reasons=['Published healthy records describe running speeds, not a rest-to-speed experiment.',
                           'Raw speed interpreted as published rad/s disagrees with published RPM ranges.',
                           'Equivalent circuit, inertia, load torque and feeder parameters are not established.'],
                  automatic_speed_rescaling=False)
    out.mkdir(parents=True, exist_ok=True)
    (out/'eligibility.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    audit(args.archive, args.out)
