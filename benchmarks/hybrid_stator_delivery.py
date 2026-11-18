"""Local cache-fix/runtime boundary and unified hybrid evidence delivery."""
import shutil
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.hybrid_stator_acceptance import producers
ROOTS=['reports/hybrid-stator-qualified-2026-11-16','reports/hybrid-stator-accepted-2026-11-16','reports/hybrid-stator-installed-replay-2026-11-16']
TESTS=Path('reports/hybrid-stator-regression-2026-11-16.xml')


def deliver(out):
    fp=package_fingerprint();anchors={p:verify(p) for p in ROOTS}
    q,a,i=[read_json(Path(p)/name) for p,name in zip(ROOTS,['summary.json','summary.json','receipt.json'])]
    gate=digest(Path('benchmarks/energy_integral_gate.py').read_bytes())
    if not all(r['passed'] and r['code_sha256']==fp for r in [q,a,i]) or q['producers']!=producers() or a['source_manifest_sha256']!=anchors[ROOTS[0]] or a['independent_reference_replays']!=12 or a['recomputed_comparisons']!=48 or a['native_generator_trips']!=24 or a['native_limiter_events']!=184 or a['immediate_trip_release_cascades']!=16 or a['native_integral_energy_checks']!=24 or a['maximum_native_integral_error_j']>.1 or a['maximum_reference_integral_error_j']>1e-4 or a['consumer_energy_gate_sha256']!=gate or len(i['rows'])!=131:raise ValueError('hybrid qualification/replay/gate binding differs')
    tests=list(ET.parse(TESTS).getroot().iter('testcase'))
    if len(tests)!=391 or any(any(c.find(k) is not None for k in ['failure','error','skipped']) for c in tests):raise ValueError('expected391 passing targeted tests')
    old=Path('reports/generator-stator-delivery-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl');wheel=Path('reports/hybrid-stator-wheel-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='e3333ce8144088272fe7bdd8faaf1d54d98fc83e9d5c791faaf92fc8c70b1e19':raise ValueError('old wheel differs')
    with ZipFile(old) as x,ZipFile(wheel) as y:
        before={p:x.read(p) for p in x.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')};after={p:y.read(p) for p in y.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed=sorted(p for p in before if before[p]!=after.get(p))
    if changed!=['power_grid_env/rms_winding.py'] or before.keys()!=after.keys() or any(Path(p).read_bytes()!=v for p,v in after.items()):raise ValueError('runtime/source boundary differs')
    cli=Path('reports/hybrid-stator-cli-2026-11-16.json');r=read_json(cli)
    if not r['completed'] or r['code_sha256']!=fp:raise ValueError('CLI differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);shutil.copyfile(wheel,root/wheel.name);(root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name);shutil.copyfile(TESTS,root/'tests.xml');shutil.copyfile(cli,root/'cli-trace.json')
    sources={Path(p) for p in producers()}|set(Path('benchmarks').glob('hybrid_stator*.py'))|set(Path('cases/hybrid-stator').glob('*'))|{Path('tests/test_hybrid_stator.py'),Path('benchmarks/energy_integral_gate.py'),Path('benchmarks/rms_equilibrium_installed_replay.py'),Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml')}
    for p in sources:
        dest=root/'sources'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),old_wheel_sha256=digest(old.read_bytes()),changed_runtime_files=changed,evidence_manifests=anchors,
      targeted_tests=391,new_hybrid_tests=12,optional_ANDES_deselected=4,native_runs=24,independent_trajectories=12,comparisons=48,native_generator_trips=24,native_limiter_events=184,immediate_trip_release_cascades=16,installed_checks=131,consumer_energy_gate_sha256=gate,independent_host=False,authenticated=False,normative_approval=False,scope=q['scope'])
    (root/'delivery.json').write_bytes(encoded(result))
    if fp!=package_fingerprint() or anchors!={p:verify(p) for p in ROOTS}:raise ValueError('evidence/runtime changed')
    seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);print(deliver(**vars(p.parse_args())))
