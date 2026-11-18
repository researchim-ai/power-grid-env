"""Bind constructed-network engineering, old corpus, public views and wheel."""
from pathlib import Path
import shutil
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from benchmarks.network_construction_acceptance import producers
from power_grid_env.study_artifacts import encoded,read_json,digest,verify,seal
from power_grid_env.evidence import package_fingerprint

ROOTS=['reports/network-construction-qualified-release-2026-11-16',
       'reports/network-construction-accepted-release-2026-11-16',
       'reports/network-construction-legacy-corpus-release-2026-11-16',
       'reports/network-construction-installed-replay-2026-11-16',
       'reports/network-construction-visual-qa-2026-11-16']


def deliver(out):
    fp=package_fingerprint();anchors={p:verify(p) for p in ROOTS}
    q,a,legacy,installed,visual=[read_json(Path(p)/name) for p,name in zip(ROOTS,('summary.json','receipt.json','corpus.json','receipt.json','receipt.json'))]
    if not all(r['passed'] and r['code_sha256']==fp for r in (q,a,installed,visual)) or legacy['producer_sha256']!=fp or q['producers']!=producers():raise ValueError('evidence/runtime mismatch')
    if q['designs']!=5 or q['dynamic_runs']!=45 or q['independent_fault_solutions']!=17 or a['verified_dynamic_runs']!=45 or a['source_manifest_sha256']!=anchors[ROOTS[0]] or visual['source_manifest_sha256']!=anchors[ROOTS[0]]:raise ValueError('construction coverage mismatch')
    if [r['status'] for r in q['rows']]!=['accepted','accepted','rejected','accepted','rejected'] or not all(r['reference']['passed'] for r in q['rows']):raise ValueError('design/reference verdict mismatch')
    if len(legacy['tasks'])!=6 or sum(len(t['options']) for t in legacy['tasks'])!=12:raise ValueError('legacy corpus incomplete')
    if len(installed['rows'])!=54 or not installed['reference_imports_blocked'] or not all(r['passed'] and r['compared'] and r['common_channels_bitwise_exact'] for r in installed['rows']):raise ValueError('installed replay differs')
    if installed['batch_manifests']!={p:verify(p) for p in installed['batch_manifests']} or len(installed['batch_manifests'])!=9:raise ValueError('composite replay batches changed')
    if len(visual['rows'])!=5:raise ValueError('diagram inventory differs')
    test=Path('reports/network-construction-regression-complete-2026-11-16.xml');cases=list(ET.parse(test).getroot().iter('testcase'))
    if len(cases)!=157 or any(c.find(k) is not None for c in cases for k in ('failure','error','skipped')):raise ValueError('157 non-skipped regressions required')
    if sum(c.attrib.get('classname','').endswith('test_network_design') for c in cases)!=32:raise ValueError('new test inventory differs')
    old=Path('reports/thermal-recovery-delivery-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    wheel=Path('reports/network-construction-wheel-release-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='04ba000ec6dc89fe86e2d43ec4826ec14b5431c7ccc7e7c9e82bb7f29a7deef9':raise ValueError('previous runtime wheel differs')
    with ZipFile(old) as x,ZipFile(wheel) as y:
        before={p:x.read(p) for p in x.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        after={p:y.read(p) for p in y.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed=sorted(p for p in before if before[p]!=after.get(p));added=sorted(after.keys()-before.keys())
    if changed!=['power_grid_env/cli.py','power_grid_env/equipment_catalog.py'] or added!=['power_grid_env/network_design.py','power_grid_env/network_design_view.py'] or before.keys()-after.keys() or any(Path(p).read_bytes()!=raw for p,raw in after.items()):raise ValueError('runtime source boundary differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(wheel,root/wheel.name);shutil.copyfile(test,root/test.name)
    (root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name)
    sources=set(Path('benchmarks').glob('network_construction*.py'))|set(Path('cases/network-construction').glob('*'))|{
        Path('tests/test_network_design.py'),Path('benchmarks/rms_equilibrium_installed_replay.py'),
        Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml'),Path('README.md'),Path('README.en.md')}
    for p in sources:
        dest=root/'sources'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),previous_wheel_sha256=digest(old.read_bytes()),
        changed_runtime_files=changed,added_runtime_files=added,evidence_manifests=anchors,
        composite_batch_manifests=installed['batch_manifests'],targeted_tests=157,new_tests=32,
        regression_xml_sha256=digest(test.read_bytes()),new_dynamic_runs=45,legacy_corpus_dynamic_runs=108,
        independent_fault_solutions=17,installed_replays=54,public_diagrams=5,
        manufacturer_authenticated=False,normative_approval=False,independent_dynamic_reference=False,independent_host=False,
        scope='Owner-constrained 400V radial construction; measured 3/4 nodes and 2/3 motors over 4s; independent static nodal/fault checks, three-grid dynamics and public SVG views. No arbitrary topology/scale, VLM training or superiority claim.')
    (root/'delivery.json').write_bytes(encoded(result))
    if fp!=package_fingerprint() or anchors!={p:verify(p) for p in ROOTS} or installed['batch_manifests']!={p:verify(p) for p in installed['batch_manifests']}:raise ValueError('evidence changed')
    seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);print(deliver(**vars(p.parse_args())))
