"""Audit the pinned Zenodo startup-current archive without executing its code.

Download source metadata and archive from https://zenodo.org/records/17048028.
This audit records eligibility, not a motor-model measurement comparison.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ARCHIVE_SHA256='bee43489518ab351b4b6eb3a5b4921f653c1f45ac050304e8a6c6c137ada38fd'
PUBLISHER_MD5='f5d813b322f30cda9a0f7ff51b056411'


def audit(archive,metadata,out):
    archive=Path(archive);metadata=Path(metadata);out=Path(out)
    if archive.stat().st_size!=11286452:raise ValueError('unexpected pinned archive size')
    content=archive.read_bytes()
    if hashlib.sha256(content).hexdigest()!=ARCHIVE_SHA256 or hashlib.md5(content).hexdigest()!=PUBLISHER_MD5:raise ValueError('pinned archive integrity failed')
    if metadata.stat().st_size>2_000_000:raise ValueError('metadata size budget')
    meta=json.loads(metadata.read_text())
    if str(meta['id'])!='17048028' or meta['metadata']['license']['id']!='cc-by-4.0':raise ValueError('unexpected record or license')
    with zipfile.ZipFile(archive) as z:
        files=[n for n in z.namelist() if not n.endswith('/') and not n.startswith('__MACOSX') and '/.' not in n]
        raw=[n for n in files if n.endswith('.dat')];healthy=[n for n in raw if '/p0_' in n]
        inventory=[dict(path=n,bytes=z.getinfo(n).file_size,sha256=hashlib.sha256(z.read(n)).hexdigest()) for n in files]
    result=dict(source='https://zenodo.org/records/17048028',doi='10.5281/zenodo.17048028',license=meta['metadata']['license'],title=meta['metadata']['title'],creators=meta['metadata']['creators'],archive_sha256=ARCHIVE_SHA256,archive_bytes=len(content),publisher_md5=PUBLISHER_MD5,archive_retained_in_repository=False,raw_records=len(raw),healthy_raw_records=len(healthy),sampling_frequency_readme_and_notebook_hz=1150.748,sampling_frequency_description_hz=1150,
        raw_format='Two tab-separated decimal-comma numeric columns, no unit header; first column time-like, second current-like. No calibration inferred.',healthy_selection='Notebook glob data/p0_*.*',inspection='Read-only ZIP inventory and source notebook inspection; notebook and MATLAB files were not executed',eligibility='insufficient_for_paired_motor_acceptance',field_validated=False,industrial_profile_qualified=False,validation_score=None,
        missing_acceptance_evidence=['Synchronized measured terminal voltage and rotor speed channels','Traceable current calibration and units for raw samples','Complete equivalent circuit, inertia, mechanical load and source-network parameters','Target plant operating conditions, event times and uncertainty protocol'],files=inventory)
    out.mkdir(parents=True,exist_ok=True)
    for filename,value in [('zenodo-source-metadata.json',meta),('public-data-eligibility.json',result)]:
        (out/filename).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive',type=Path,required=True);p.add_argument('--metadata',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();r=audit(a.archive,a.metadata,a.out);print(r['eligibility'])


if __name__=='__main__':main()
