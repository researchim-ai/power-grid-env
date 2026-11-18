"""Bounded installed-wheel replay; large batches are separately sealed siblings.

The composite receipt anchors each batch without nesting >256 MB of duplicated
expected/native traces inside one artifact. Existing integrity budgets stay fixed.
"""
from pathlib import Path
import copy
from power_grid_env.study_artifacts import encoded,read_json,digest,seal,verify
from power_grid_env.evidence import package_fingerprint


def compact(report):return {k:report[k] for k in ('completed','time_s','sample_side','event_log','channels')}


def prepare(source,legacy,out):
    from benchmarks.network_construction_acceptance import variants
    from power_grid_env.network_design import compile_network
    source,legacy,out=Path(source).resolve(),Path(legacy).resolve(),Path(out).resolve()
    anchors={str(p):verify(p) for p in (source,legacy)};fp=package_fingerprint()
    if read_json(source/'summary.json')['code_sha256']!=fp:raise ValueError('qualification runtime differs')
    records=[]
    for job in variants():
        plan=compile_network(job['task'],job['proposal'])[2]
        for case in plan.cases:
            for level in range(3):
                leaf=source/job['id']/'studies'/case.id/('grid-'+str(level))
                expected=case.study.model_dump(mode='json');expected['induction']['step_s']/=2**level
                if read_json(leaf/'study.json')!=expected:raise ValueError('compiled replay recipe differs')
                records.append(dict(id=job['id']+'-'+case.id+'-'+str(level),kind='coupled',study=expected,expected=compact(read_json(leaf/'result.json'))))
    corpus=read_json(legacy/'corpus.json')
    if corpus['producer_sha256']!=fp:raise ValueError('legacy corpus runtime differs')
    first=corpus['tasks'][0];folder=legacy/'tasks'/first['id']/first['options'][0]['option_id']/'studies'
    plan=read_json(folder/'input.json')
    for case in plan['cases']:
        for level in range(3):
            leaf=folder/case['id']/('grid-'+str(level))
            records.append(dict(id='legacy-'+case['id']+'-'+str(level),kind='coupled',study=read_json(leaf/'study.json'),expected=compact(read_json(leaf/'result.json'))))
    if len(records)!=54 or len({r['id'] for r in records})!=54:raise ValueError('expected 45 new and 9 legacy studies')
    out.mkdir(parents=True,exist_ok=False);batches=[]
    for first in range(0,len(records),6):
        batch=out.with_name(out.name+'-batch-'+str(first//6));batch.mkdir();chunk=records[first:first+6];raw=encoded(chunk)
        if len(raw)>60_000_000:raise ValueError('bounded replay JSON exceeded')
        (batch/'records.json').write_bytes(raw)
        batches.append(dict(path=str(batch),records_sha256=digest(raw),ids=[r['id'] for r in chunk]))
    result=dict(code_sha256=fp,source_manifests=anchors,batches=batches,
                consumer_sha256=digest(Path('benchmarks/rms_equilibrium_installed_replay.py').read_bytes()))
    (out/'preparation.json').write_bytes(encoded(result));return result


def finish(out):
    root=Path(out);pre=read_json(root/'preparation.json');fp=package_fingerprint()
    if (root/'receipt.json').exists() or (root/'manifest.json').exists():raise ValueError('fresh receipt required')
    if pre['code_sha256']!=fp or pre['consumer_sha256']!=digest(Path('benchmarks/rms_equilibrium_installed_replay.py').read_bytes()):raise ValueError('runtime/consumer changed')
    anchors={};rows=[]
    for b in pre['batches']:
        p=Path(b['path']);anchors[str(p)]=verify(p);receipt=read_json(p/'receipt.json')
        if digest((p/'records.json').read_bytes())!=b['records_sha256'] or not receipt['passed'] or receipt['code_sha256']!=fp or not receipt['reference_imports_blocked'] or [r['id'] for r in receipt['rows']]!=b['ids']:raise ValueError('batch identity differs')
        if not all(r['passed'] and r['compared'] and r['common_channels_bitwise_exact'] for r in receipt['rows']):raise ValueError('installed result differs')
        rows+=receipt['rows']
    if len(rows)!=54 or pre['source_manifests']!={p:verify(p) for p in pre['source_manifests']}:raise ValueError('source inventory changed')
    result=dict(passed=True,code_sha256=fp,new_records=45,legacy_records=9,rows=rows,batch_manifests=anchors,
                source_manifests=pre['source_manifests'],reference_imports_blocked=True,independent_host=False,
                scope='Composite same-host Python-I installed motor-study replay; separately sealed 6-record batches, exact axes/events/common channels; no authentication or independent host')
    (root/'receipt.json').write_bytes(encoded(result));seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','finish']);p.add_argument('--source');p.add_argument('--legacy');p.add_argument('--out',required=True);a=p.parse_args()
    r=prepare(a.source,a.legacy,a.out) if a.action=='prepare' else finish(a.out)
    print(dict(batches=len(r.get('batches',r.get('batch_manifests',{}))),passed=r.get('passed')))
