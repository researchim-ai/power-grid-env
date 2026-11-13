"""Reassess sealed trajectories after a qualification-only source change.

No numerical trajectories are rerun. The original wheel must reproduce the
recorded simulation package fingerprint. Every current top-level runtime Python
file except the qualification scorer must have identical bytes to that wheel.
The output retains both producers and never calls this numerical replay.
"""
import argparse
import hashlib
from pathlib import Path
import zipfile
from power_grid_env import switching_convergence as engine
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


def compatible_wheel(wheel,expected):
    wheel=Path(wheel)
    if wheel.stat().st_size>16_000_000:raise ValueError('oversized producer wheel')
    with zipfile.ZipFile(wheel) as archive:
        entries=[x for x in archive.infolist() if x.filename.startswith('power_grid_env/') and x.filename.count('/')==1 and x.filename.endswith('.py')]
        if len({x.filename for x in entries})!=len(entries) or any(x.file_size>2_000_000 for x in entries) or sum(x.file_size for x in entries)>16_000_000:raise ValueError('unsafe producer wheel source inventory')
        source={Path(x.filename).name:archive.read(x) for x in entries}
    digest=hashlib.sha256()
    for name,content in sorted(source.items()):digest.update(name.encode());digest.update(content)
    if digest.hexdigest()!=expected:raise ValueError('wheel does not match recorded waveform producer')
    current={p.name:p.read_bytes() for p in Path(engine.__file__).parent.glob('*.py')}
    if source.keys()!=current.keys():raise ValueError('runtime module inventory changed')
    changed=[name for name in source if source[name]!=current[name]]
    if any(name!='switching_convergence.py' for name in changed):raise ValueError('numerical runtime changed: '+','.join(changed))
    return dict(identical_numerical_runtime=True,changed_files=sorted(changed),wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),waveform_code_sha256=expected,assessment_code_sha256=package_fingerprint())


def run(source,wheel,out):
    source=Path(source);out=Path(out);original_manifest=verify(source);old=read_json(source/'summary.json');before=package_fingerprint();compatibility=compatible_wheel(wheel,old['code_sha256']);protocol=read_json(source/'protocol.json');study=read_json(source/'study.json');calls=[]
    original=engine.run_switching_drive
    def saved(doc):
        index=len(calls);folder=source/f'level-{index}'
        if encoded(read_json(folder/'study.json'))!=encoded(doc):raise ValueError('reassessment inputs differ from sealed trajectory')
        calls.append(index);return read_json(folder/'result.json')
    engine.run_switching_drive=saved
    try:assessment=engine.qualify_switching_drive(study,max_levels=protocol['max_levels'],limits=protocol['limits'])
    finally:engine.run_switching_drive=original
    if assessment['qualified']!=old['qualified'] or assessment['selected_step_s']!=old['selected_step_s'] or len(assessment['attempts'])!=len(old['attempts']):raise ValueError('reassessment outcome differs')
    if package_fingerprint()!=before or verify(source)!=original_manifest:raise ValueError('reassessment producer/source changed')
    out.mkdir(parents=True,exist_ok=False)
    result=dict(schema='power-grid-env.switching-qualification-reassessment/v1',completed=True,compatibility=compatibility,assessment=assessment,source_manifest_sha256=original_manifest,source_qualified=old['qualified'],trajectories_reassessed=len(calls),new_numerical_trajectories=0,numerical_replay=False,scope='sealed waveform reassessment with byte-identical numerical modules; qualification scorer may differ; integrity is not authenticated provenance')
    (out/'reassessment.json').write_bytes(encoded(result));identity=seal(out);print(encoded(dict(completed=True,qualified=assessment['qualified'],manifest_sha256=identity,new_numerical_trajectories=0)).decode(),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--producer-wheel',required=True);parser.add_argument('--out',required=True);args=parser.parse_args();run(args.source,args.producer_wheel,args.out)
