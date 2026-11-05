"""Streamed CSC primary against pinned independent native OEL and ESST networks."""
import argparse,json
from pathlib import Path
from benchmarks.sparse_jacobian_validation import case
from benchmarks.oel2c_jacobian_native_validation import qualify as oel_native
from benchmarks.esst1a_jacobian_native_validation import qualify as esst_native
from benchmarks.native_online_validation import source_identity
from power_grid_env.evidence import package_fingerprint


def qualify(out,root):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=source_identity()
    (out/'protocol.json').write_text(json.dumps(dict(code_sha256=code,source_sha256=sources,cases=['oel_regulating','batch_four','esst_trip'],native_tolerances=[1e-12,1e-13],eight_machine_native_qualified=False),indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps(dict(passed=False,status='running'))+'\n')
    try:
        left=oel_native(out/'oel',root,names=('oel_regulating','batch_four'),case_factory=case)
        right=esst_native(out/'esst',root,names=('esst_trip',),case_factory=case)
        for folder,names in [('oel',['oel_regulating','batch_four']),('esst',['esst_trip'])]:
            for name in names:
                p=json.loads((out/folder/name/'primary.json').read_text())
                if p['integration']['jacobian_assembly']!='column_blocks':raise ValueError('native primary did not use streamed assembly')
        if code!=package_fingerprint() or sources!=source_identity():raise ValueError('native producer changed')
        result=dict(passed=left['passed'] and right['passed'],status='complete',code_sha256=code,source_sha256=sources,native_cases=3,native_fresh_processes=6,eight_machine_native_qualified=False,industrial_release_qualified=False,independent_host_verified=False)
        (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');return result
    except BaseException as exc:
        (out/'summary.json').write_text(json.dumps(dict(passed=False,status='failed',error=type(exc).__name__+': '+str(exc)),indent=2)+'\n');raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args();qualify(a.out,a.root)
