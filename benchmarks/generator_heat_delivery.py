"""Local-only source/wheel/evidence binding for bounded mixed thermal machines."""
from pathlib import Path
from zipfile import ZipFile
import shutil
import xml.etree.ElementTree as ET
from power_grid_env.study_artifacts import encoded,read_json,digest,seal,verify
from power_grid_env.evidence import package_fingerprint

ROOTS=['reports/generator-heat-final-qualified-2026-11-16','reports/generator-heat-accepted-2026-11-16','reports/generator-heat-installed-replay-2026-11-16']
TESTS=['reports/generator-heat-regression-2026-11-16.xml','reports/generator-heat-final-unit-tests-2026-11-16.xml']


def deliver(out):
    anchors={p:verify(p) for p in ROOTS};fp=package_fingerprint();q,a,i=[read_json(Path(p)/name) for p,name in zip(ROOTS,['summary.json','summary.json','receipt.json'])]
    if not all(r['passed'] and r['code_sha256']==fp for r in [q,a,i]) or a['source_manifest_sha256']!=anchors[ROOTS[0]] or a['recomputed_comparisons']!=32 or a['independent_reference_replays']!=8 or a['native_refusal_replays']!=8 or a['full_physical_spectra_checked']!=8 or len(i['rows'])!=79:raise ValueError('qualification/replay binding differs')
    if any(digest(Path(p).read_bytes())!=h for p,h in q['producers'].items()):raise ValueError('producer differs')
    nodes=set();executions=0
    for p in TESTS:
        for case in ET.parse(p).getroot().iter('testcase'):
            if any(case.find(k) is not None for k in ['failure','error','skipped']):raise ValueError('regression failed/skipped')
            nodes.add((case.get('classname'),case.get('name')));executions+=1
    if len(nodes)!=324 or executions!=337:raise ValueError('expected324 unique targeted tests/337 executions')
    old=Path('reports/generator-replica-delivery-2026-11-15/power_grid_env-0.1.0-py3-none-any.whl');wheel=Path('reports/generator-heat-wheel-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='4768a3adee606080cad70bccc9a57c07ab96c8f568410c0d2b1a2ae670ef786e':raise ValueError('old wheel differs')
    with ZipFile(old) as z,ZipFile(wheel) as w:
        before={p:z.read(p) for p in z.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')};after={p:w.read(p) for p in w.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed=sorted(p for p in before.keys()&after.keys() if before[p]!=after[p]);added=sorted(after.keys()-before.keys())
    if changed!=['power_grid_env/coupled_initialization.py','power_grid_env/coupled_rms.py','power_grid_env/dae_linearization.py','power_grid_env/mixed_winding.py','power_grid_env/mixed_winding_equilibrium.py'] or added!=['power_grid_env/generator_heat.py'] or before.keys()-after.keys() or any(Path(p).read_bytes()!=v for p,v in after.items()):raise ValueError('runtime/source boundary differs')
    cli=Path('reports/generator-heat-cli-2026-11-16.json');report=read_json(cli)
    if not report['completed'] or report['code_sha256']!=fp:raise ValueError('CLI failed or runtime differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);shutil.copyfile(wheel,root/wheel.name);(root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name);shutil.copyfile(cli,root/'cli-trace.json')
    sources=set(Path(p) for p in q['producers'])|set(Path('benchmarks').glob('generator_heat*.py'))|set(Path('cases/generator-heat').glob('*'))|{Path('tests/test_generator_heat.py'),Path('benchmarks/rms_equilibrium_installed_replay.py'),Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml')}
    for p in sources:
        target=root/'sources'/p;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    for p in TESTS:shutil.copyfile(p,root/Path(p).name)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),old_wheel_sha256=digest(old.read_bytes()),changed_runtime_files=changed,added_runtime_files=added,evidence_manifests=anchors,unique_targeted_tests=324,test_executions=executions,optional_ANDES_deselected=4,native_initial_refusals=8,native_runs=16,independent_trajectories=8,comparisons=32,installed_checks=79,independent_host=False,authenticated=False,normative_approval=False,scope=q['scope'])
    (root/'delivery.json').write_bytes(encoded(result))
    if package_fingerprint()!=fp or {p:verify(p) for p in ROOTS}!=anchors:raise ValueError('evidence changed')
    seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);print(deliver(**vars(p.parse_args())))
