"""Local-only source/wheel/evidence binding for bounded mixed thermal machines."""
from pathlib import Path
from zipfile import ZipFile
import shutil
import xml.etree.ElementTree as ET
from power_grid_env.study_artifacts import encoded,read_json,digest,seal,verify
from power_grid_env.evidence import package_fingerprint

ROOTS=['reports/mixed-winding-qualified-2026-11-15','reports/mixed-winding-accepted-2026-11-15','reports/mixed-winding-installed-replay-2026-11-15']
TESTS=['reports/mixed-winding-regression-2026-11-15.xml','reports/mixed-winding-legacy-regression-2026-11-15.xml','reports/mixed-winding-final-extra-tests-2026-11-15.xml']


def deliver(out):
    anchors={p:verify(p) for p in ROOTS};fp=package_fingerprint();q,a,i=[read_json(Path(p)/name) for p,name in zip(ROOTS,['summary.json','summary.json','receipt.json'])]
    if not all(r['passed'] and r['code_sha256']==fp for r in [q,a,i]) or a['source_manifest_sha256']!=anchors[ROOTS[0]] or a['recomputed_comparisons']!=64 or a['independent_reference_replays']!=16 or len(i['rows'])!=14:raise ValueError('qualification/replay binding differs')
    if any(digest(Path(p).read_bytes())!=h for p,h in q['producers'].items()):raise ValueError('producer differs')
    nodes=set();executions=0
    for p in TESTS:
        for case in ET.parse(p).getroot().iter('testcase'):
            if any(case.find(k) is not None for k in ['failure','error','skipped']):raise ValueError('regression failed/skipped')
            nodes.add((case.get('classname'),case.get('name')));executions+=1
    if len(nodes)!=850 or executions!=858:raise ValueError('expected850 unique targeted tests/858 executions')
    old=Path('reports/two-cage-winding-delivery-2026-11-15/power_grid_env-0.1.0-py3-none-any.whl');wheel=Path('reports/mixed-winding-wheel-2026-11-15/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='2b9b3714c4aa2ab5d59eb21c10c051b84e80b75880dc7b2228b6d0a49b3b2c44':raise ValueError('old wheel differs')
    with ZipFile(old) as z,ZipFile(wheel) as w:
        before={p:z.read(p) for p in z.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')};after={p:w.read(p) for p in w.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed=sorted(p for p in before.keys()&after.keys() if before[p]!=after[p]);added=sorted(after.keys()-before.keys())
    if changed!=['power_grid_env/coupled_initialization.py','power_grid_env/coupled_rms.py','power_grid_env/dae.py','power_grid_env/rms_winding.py'] or added!=['power_grid_env/mixed_winding.py'] or before.keys()-after.keys() or any(Path(p).read_bytes()!=v for p,v in after.items()):raise ValueError('runtime/source boundary differs')
    cli=Path('reports/mixed-winding-cli-2026-11-15.json');report=read_json(cli)
    if not report['completed'] or report['code_sha256']!=fp:raise ValueError('CLI failed or runtime differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);shutil.copyfile(wheel,root/wheel.name);(root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name);shutil.copyfile(cli,root/'cli-trace.json')
    sources=set(Path(p) for p in q['producers'])|set(Path('benchmarks').glob('mixed_winding*.py'))|set(Path('cases/mixed-winding').glob('*'))|{Path('tests/test_mixed_winding.py'),Path('benchmarks/rms_equilibrium_installed_replay.py'),Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml')}
    for p in sources:
        target=root/'sources'/p;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    for p in TESTS:shutil.copyfile(p,root/Path(p).name)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),old_wheel_sha256=digest(old.read_bytes()),changed_runtime_files=changed,added_runtime_files=added,evidence_manifests=anchors,unique_targeted_tests=850,test_executions=858,optional_ANDES_deselected=4,native_runs=32,independent_trajectories=16,comparisons=64,installed_checks=14,independent_host=False,authenticated=False,normative_approval=False,scope=q['scope'])
    (root/'delivery.json').write_bytes(encoded(result))
    if package_fingerprint()!=fp or {p:verify(p) for p in ROOTS}!=anchors:raise ValueError('evidence changed')
    seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);print(deliver(**vars(p.parse_args())))
