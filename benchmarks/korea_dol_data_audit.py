"""Inspect the author's healthy DOL archive, without executing MATLAB code.

Public accessibility does not establish a redistribution license or SI calibration.
The raw archive stays in the caller's cache; only an inventory is written.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile
import numpy as np
from scipy.io import loadmat

SHA256 = '00879d2fb341af2ba55b23910f3be4d489177d9af88bd24d6392fe47736946e7'
SIZE = 20304231
SOURCE = 'https://home.eecs.korea.ac.kr/publications/database'
FILE_ID = '1cfcv5BCVpEh0O-Bh0h_-41R0R6Rs7Oc3'
LOADS = ('0', '50', '75', '100')
SIGNALS = ('cur1', 'cur2', 'vol1', 'vol2', 'airgflux', 'leakflux', 'Ver')
EXPECTED = {f'Healthy_DOLstart_{load}_{signal}.mat' for load in LOADS for signal in SIGNALS}


def inspect_member(name: str, content: bytes) -> dict:
    if name not in EXPECTED:
        raise ValueError('unexpected DOL member')
    if len(content) > 2_000_000:
        raise ValueError('MAT member budget exceeded')
    data = loadmat(io.BytesIO(content))
    keys = [k for k in data if not k.startswith('__')]
    signal = name.rsplit('_', 1)[1][:-4]
    expected_key = {'Ver': 'ac0', 'airgflux': 'airgflk', 'leakflux': 'leakflk'}.get(signal, signal)
    if keys != [expected_key]:
        raise ValueError('unexpected MAT variable layout')
    a = data[expected_key]
    if a.shape != (100000, 1) or a.dtype.kind != 'f' or not np.isfinite(a).all():
        raise ValueError('expected finite 100000-row sensor column')
    return dict(variable=expected_key, shape=list(a.shape), dtype=str(a.dtype),
                minimum_raw=float(a.min()), maximum_raw=float(a.max()),
                rms_raw=float(np.sqrt(np.mean(a*a))), sha256=hashlib.sha256(content).hexdigest(),
                si_units_verified=False, calibration_applied=False)


def audit(archive: Path, out: Path) -> dict:
    archive, out = Path(archive), Path(out)
    if archive.stat().st_size != SIZE or hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError('pinned DOL archive integrity failed')
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        if len(members) != len(EXPECTED) or {m.filename for m in members} != EXPECTED:
            raise ValueError('unexpected DOL archive inventory')
        if any(m.file_size > 2_000_000 or m.flag_bits & 1 for m in members):
            raise ValueError('unsupported encrypted or oversized DOL member')
        records = {m.filename: inspect_member(m.filename, z.read(m)) for m in members}
    result = dict(source_url=SOURCE, google_drive_file_id=FILE_ID,
                  publication_doi='10.1109/JSEN.2024.3447869', archive_sha256=SHA256,
                  archive_bytes=SIZE, records=records,
                  contains_startup_records_by_publisher_description=True,
                  raw_archive_redistributed=False, license_verified=False,
                  speed_channel_present=False, explicit_time_channel_present=False,
                  sampling_rate_hz_verified=None,
                  related_plot_script_hints=dict(sampling_rate_hz=10240, voltage_multiplier=100, current_multiplier=100,
                      filenames_reference_steady_DOL60hz=True, applicable_to_startup_archive_verified=False),
                  measured_startup_qualified=False,
                  field_validated=False, industrial_profile_qualified=False,
                  reasons=['No reuse/redistribution license established from inspected landing page/archive.',
                           'No calibrated SI voltage/current mapping established.',
                           'No explicit time or speed variable in the 28 MAT files.',
                           'Only two current and two voltage columns per nominal load in this archive.',
                           'Motor circuit, inertia, feeder and load torque not established.'])
    out.mkdir(parents=True, exist_ok=True)
    (out/'eligibility.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    audit(args.archive, args.out)
