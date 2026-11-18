"""36 installed native replays in six separately sealed bounded batches."""
import argparse
from pathlib import Path
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,digest,seal,verify


def run(source,source_manifest,out,runtime_dir):
    if verify(source)!=source_manifest:raise ValueError('owner source differs')
    summary=read_json(Path(source)/'summary.json')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    records=[]
    for d in summary['rows']:
        folder=Path(source)/d['id']
        for c in read_json(folder/'plan.json')['cases']:
            for grid in range(3):
                leaf=folder/'studies'/c['id']/('grid-'+str(grid))
                report=read_json(leaf/'result.json')
                records.append(dict(id='r'+str(len(records)),kind='coupled',study=read_json(leaf/'study.json'),
                    expected={k:report[k] for k in ('completed','time_s','sample_side','event_log','channels')}))
    if len(records)!=36:raise ValueError('36 source studies required')
    batches=[]
    for i in range(0,len(records),6):
        folder=root.with_name(root.name+'-batch-'+str(i//6));folder.mkdir()
        raw=encoded(records[i:i+6])
        if len(raw)>60_000_000:raise ValueError('replay batch byte budget exceeded')
        (folder/'records.json').write_bytes(raw);batches.append(folder)
    consumer=Path('benchmarks/rms_equilibrium_installed_replay.py')
    consumer_sha=digest(consumer.read_bytes())
    def execute(folder):
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        with (folder.with_suffix('.log')).open('w') as log:
            subprocess.run([sys.executable,'-I',str(consumer),'--installed-dir',runtime_dir,'--data-dir',str(folder)],
                           env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        receipt=read_json(folder/'receipt.json')
        if (not receipt['passed'] or receipt['code_sha256']!=package_fingerprint() or not receipt['reference_imports_blocked'] or
                len(receipt['rows'])!=6 or not all(v['compared'] and v['common_channels_bitwise_exact'] for v in receipt['rows'])):
            raise ValueError('installed trajectory not exact')
        print('installed batch exact',folder.name,flush=True)
        return dict(path=str(folder),manifest_sha256=verify(folder),rows=6)
    with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(execute,batches))
    if digest(consumer.read_bytes())!=consumer_sha or verify(source)!=source_manifest:raise ValueError('source/consumer changed')
    result=dict(passed=True,code_sha256=package_fingerprint(),source_manifest_sha256=source_manifest,
        source_physics_code_sha256=summary['code_sha256'],consumer_sha256=consumer_sha,
        records=36,matched_blas_environment={k:os.environ.get(k) for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS')},batches=rows,channels_and_events_bitwise_exact=True,reference_imports_blocked=True,
        independent_host=False,scope='Same-host installed source-to-replay physics identity; six bounded sibling artifacts.')
    (root/'receipt.json').write_bytes(encoded(result));seal(root)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for k in ('source','source-manifest','out','runtime-dir'):p.add_argument('--'+k,required=True)
    print(run(**vars(p.parse_args())))
