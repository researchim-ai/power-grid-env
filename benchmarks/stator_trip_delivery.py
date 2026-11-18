"""Bind independent limiter qualification to the unchanged R06.49 wheel."""
from pathlib import Path
from zipfile import ZipFile
import shutil
import xml.etree.ElementTree as ET
from power_grid_env.study_artifacts import read_json,encoded,digest,seal,verify
from power_grid_env.evidence import package_fingerprint
from benchmarks.stator_trip_acceptance import producers

ROOTS=['reports/stator-trip-qualified-2026-11-16','reports/stator-trip-accepted-final-2026-11-16','reports/stator-trip-installed-replay-2026-11-16']
TESTS=[Path('reports/stator-trip-regression-2026-11-16.xml'),Path('reports/stator-trip-final-unit-tests-2026-11-16.xml')]
PREVIOUS=Path('reports/generator-stator-delivery-2026-11-16')


def deliver(out):
    anchors={p:verify(p) for p in ROOTS};previous_anchor=verify(PREVIOUS);fp=package_fingerprint()
    q,a,i=[read_json(Path(p)/name) for p,name in zip(ROOTS,['summary.json','summary.json','receipt.json'])]
    if not all(r['passed'] and r['code_sha256']==fp for r in [q,a,i]) or q['producers']!=producers() or a['source_manifest_sha256']!=anchors[ROOTS[0]] or a['independent_reference_replays']!=12 or a['recomputed_comparisons']!=48 or a['native_generator_trips']!=24 or a['posttrip_cooling_points_checked']!=252 or len(i['rows'])!=113 or a['native_integral_energy_checks']!=24 or a['maximum_native_integral_error_j']>.1 or a['maximum_reference_integral_error_j']>1e-4:raise ValueError('qualification/replay coverage or producer differs')
    tests=[c for p in TESTS for c in ET.parse(p).getroot().iter('testcase')];nodes={(c.get('classname'),c.get('name')) for c in tests}
    if len(nodes)!=173 or len(tests)!=189 or any(any(c.find(k) is not None for k in ['error','failure','skipped']) for c in tests):raise ValueError('expected173 unique targeted tests/189 passing executions')
    wheel=PREVIOUS/'power_grid_env-0.1.0-py3-none-any.whl';old=read_json(PREVIOUS/'delivery.json')
    if old['code_sha256']!=fp or digest(wheel.read_bytes())!='e3333ce8144088272fe7bdd8faaf1d54d98fc83e9d5c791faaf92fc8c70b1e19':raise ValueError('qualified runtime/wheel changed')
    with ZipFile(wheel) as z:
        if any(Path(p).read_bytes()!=z.read(p) for p in z.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')):raise ValueError('checkout runtime differs from unchanged wheel')
    cli=Path('reports/stator-trip-cli-2026-11-16.json');r=read_json(cli)
    if not r['completed'] or r['code_sha256']!=fp:raise ValueError('CLI failed or runtime differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);shutil.copyfile(wheel,root/wheel.name);
    for p in TESTS:shutil.copyfile(p,root/p.name)
    shutil.copyfile(cli,root/'cli-trace.json')
    sources={Path(p) for p in producers()}|set(Path('benchmarks').glob('stator_trip*.py'))|set(Path('cases/stator-trip').glob('*'))|{Path('tests/test_stator_trip.py'),Path('benchmarks/rms_equilibrium_installed_replay.py'),Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml')}
    for p in sources:
        dest=root/'sources'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),unchanged_runtime=True,previous_runtime_delivery_sha256=previous_anchor,evidence_manifests=anchors,
      targeted_tests=173,test_executions=189,new_trip_tests=18,native_runs=24,independent_trajectories=12,comparisons=48,native_generator_trips=24,installed_checks=113,
      independent_host=False,authenticated=False,normative_approval=False,scope=q['scope'])
    (root/'delivery.json').write_bytes(encoded(result))
    if package_fingerprint()!=fp or {p:verify(p) for p in ROOTS}!=anchors or verify(PREVIOUS)!=previous_anchor:raise ValueError('evidence changed')
    seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);print(deliver(**vars(p.parse_args())))
