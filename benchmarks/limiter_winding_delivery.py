"""Bind independent limiter qualification to the unchanged R06.43 wheel."""
from pathlib import Path
from zipfile import ZipFile
import shutil
import xml.etree.ElementTree as ET
from power_grid_env.study_artifacts import read_json,encoded,digest,seal,verify
from power_grid_env.evidence import package_fingerprint
from benchmarks.limiter_winding_acceptance import producers

ROOTS=['reports/limiter-winding-qualified-2026-11-15','reports/limiter-winding-accepted-2026-11-15','reports/limiter-winding-installed-replay-2026-11-15']
TESTS=Path('reports/limiter-winding-regression-2026-11-15.xml')
PREVIOUS=Path('reports/controlled-winding-delivery-2026-11-15')


def deliver(out):
    anchors={p:verify(p) for p in ROOTS};previous_anchor=verify(PREVIOUS);fp=package_fingerprint()
    q,a,i=[read_json(Path(p)/name) for p,name in zip(ROOTS,['summary.json','summary.json','receipt.json'])]
    if not all(r['passed'] and r['code_sha256']==fp for r in [q,a,i]) or q['producers']!=producers() or a['source_manifest_sha256']!=anchors[ROOTS[0]] or a['independent_reference_replays']!=16 or a['recomputed_comparisons']!=64 or a['native_limiter_events']!=192 or a['command_permutation_pairs']!=8 or len(i['rows'])!=43:raise ValueError('qualification/replay coverage or producer differs')
    tests=list(ET.parse(TESTS).getroot().iter('testcase'))
    if len(tests)!=142 or any(any(c.find(k) is not None for k in ['error','failure','skipped']) for c in tests):raise ValueError('expected142 passing targeted tests')
    wheel=PREVIOUS/'power_grid_env-0.1.0-py3-none-any.whl';old=read_json(PREVIOUS/'delivery.json')
    if old['code_sha256']!=fp or digest(wheel.read_bytes())!='3b8e22d92f231e2c0af4dcb9455157f3882fa050825075f166bb7dc9553e6ec7':raise ValueError('qualified runtime/wheel changed')
    with ZipFile(wheel) as z:
        if any(Path(p).read_bytes()!=z.read(p) for p in z.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')):raise ValueError('checkout runtime differs from unchanged wheel')
    cli=Path('reports/limiter-winding-cli-2026-11-15.json');r=read_json(cli)
    if not r['completed'] or r['code_sha256']!=fp:raise ValueError('CLI failed or runtime differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);shutil.copyfile(wheel,root/wheel.name);shutil.copyfile(TESTS,root/'tests.xml');shutil.copyfile(cli,root/'cli-trace.json')
    sources={Path(p) for p in producers()}|set(Path('benchmarks').glob('limiter_winding*.py'))|set(Path('cases/limiter-winding').glob('*'))|{Path('tests/test_limiter_winding.py'),Path('benchmarks/rms_equilibrium_installed_replay.py'),Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml')}
    for p in sources:
        dest=root/'sources'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),unchanged_runtime=True,previous_runtime_delivery_sha256=previous_anchor,evidence_manifests=anchors,
      targeted_tests=142,new_limiter_tests=13,native_runs=32,independent_trajectories=16,comparisons=64,native_limiter_events=192,installed_checks=43,
      independent_host=False,authenticated=False,normative_approval=False,scope=q['scope'])
    (root/'delivery.json').write_bytes(encoded(result))
    if package_fingerprint()!=fp or {p:verify(p) for p in ROOTS}!=anchors or verify(PREVIOUS)!=previous_anchor:raise ValueError('evidence changed')
    seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);print(deliver(**vars(p.parse_args())))
