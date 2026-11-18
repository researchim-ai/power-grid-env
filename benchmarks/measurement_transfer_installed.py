"""Prepare/finalize bounded batches for the existing Python-I wheel consumer."""
from pathlib import Path
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest
from power_grid_env.evidence import package_fingerprint


def compact(trace):
    return {k:trace[k] for k in ('completed','time_s','sample_side','event_log','channels')}


def prepare(source,legacy,out):
    from benchmarks.measurement_transfer_cases import jobs
    from power_grid_env.study_spec import StudySpec
    import copy
    source,legacy,out=Path(source),Path(legacy),Path(out)
    anchors={str(p):verify(p) for p in (source,legacy)};records=[]
    summary=read_json(source/'summary.json');fp=package_fingerprint()
    if not summary['passed'] or summary['code_sha256']!=fp:raise ValueError('qualification runtime mismatch')
    for job in jobs():
        for method in ('radau','network_dae'):
            for grid in (0,1):
                leaf=source/job['id']/(method+'-'+str(grid));verify(leaf)
                owner=read_json(leaf/'study.json');expected=copy.deepcopy(job['study'])
                expected['coupled'].update(integration_method=method,step_s=.002/2**grid)
                if owner!=StudySpec.model_validate(expected).model_dump(mode='json'):raise ValueError('study recipe differs')
                records.append(dict(id=job['id']+'-'+method+'-'+str(grid),kind='coupled',study=owner,expected=compact(read_json(leaf/'trace.json'))))
    old=[record for batch in sorted(legacy.glob('batch-*')) for record in read_json(batch/'records.json')]
    # Prior protected reclose cycles in both heat policies, plus prior 12 unified
    # limiter/I2-trip cases. Their sealed owner/trace bytes are the anchors.
    selected=old[:4]+old[32:36]+old[-12:]
    for record in selected:
        records.append(dict(id='legacy-'+record['id'],kind=record['kind'],study=record['study'],expected=compact(record['expected'])))
    if len(records)!=108 or len({r['id'] for r in records})!=108:raise ValueError('expected 88 new and 20 legacy records')
    out.mkdir(parents=True,exist_ok=False);batches=[]
    for first in range(0,len(records),32):
        batch=out/('batch-'+str(first//32));batch.mkdir();chunk=records[first:first+32]
        raw=encoded(chunk)
        if len(raw)>60_000_000:raise ValueError('replay batch exceeds safe JSON budget')
        (batch/'records.json').write_bytes(raw);batches.append(dict(id=batch.name,records_sha256=digest(raw),ids=[r['id'] for r in chunk]))
    data=dict(code_sha256=fp,source_manifests=anchors,batches=batches,new_records=88,legacy_records=20,
              consumer_sha256=digest(Path('benchmarks/rms_equilibrium_installed_replay.py').read_bytes()))
    (out/'preparation.json').write_bytes(encoded(data));return data


def finish(out):
    out=Path(out)
    if (out/'manifest.json').exists() or (out/'receipt.json').exists():raise ValueError('fresh receipt required; sealed replay cannot be overwritten')
    pre=read_json(out/'preparation.json');fp=package_fingerprint();rows=[];anchors={}
    if fp!=pre['code_sha256']:raise ValueError('runtime changed')
    for b in pre['batches']:
        root=out/b['id'];anchors[b['id']]=verify(root)
        if digest((root/'records.json').read_bytes())!=b['records_sha256']:raise ValueError('records changed')
        receipt=read_json(root/'receipt.json')
        if not receipt['passed'] or receipt['code_sha256']!=fp or not receipt['reference_imports_blocked'] or [r['id'] for r in receipt['rows']]!=b['ids']:
            raise ValueError('batch replay failed')
        if not all(r['passed'] and r['compared'] and r['common_channels_bitwise_exact'] for r in receipt['rows']):
            raise ValueError('installed trace differs')
        rows+=receipt['rows']
    if len(rows)!=108 or pre['source_manifests']!={p:verify(p) for p in pre['source_manifests']}:raise ValueError('source inventory changed')
    receipt=dict(passed=True,code_sha256=fp,rows=rows,batch_manifests=anchors,new_records=88,legacy_records=20,
        reference_imports_blocked=True,independent_host=False,scope='Python-I installed wheel on same host; 32-record bounded batches, exact event/axis/channel replay. Not authentication or independent hardware.')
    (out/'receipt.json').write_bytes(encoded(receipt));seal(out);return receipt


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','finish']);p.add_argument('--out',required=True)
    p.add_argument('--source');p.add_argument('--legacy');a=p.parse_args()
    if a.action=='prepare':
        data=prepare(a.source,a.legacy,a.out)
        print(dict(batches=len(data['batches']),new_records=data['new_records'],legacy_records=data['legacy_records']))
    else:print(dict(passed=finish(a.out)['passed']))
