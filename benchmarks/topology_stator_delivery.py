"""Bind the R(T) current-tangent fix, topology evidence and local wheel replay."""
import shutil
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.topology_stator_acceptance import producers

ROOTS = ['reports/topology-stator-qualified-2026-11-16',
         'reports/topology-stator-accepted-2026-11-16',
         'reports/topology-stator-installed-replay-2026-11-16']
TESTS = [Path('reports/topology-stator-regression-2026-11-16.xml'),
         Path('reports/topology-stator-physical-regression-2026-11-16.xml')]


def deliver(out):
    fp=package_fingerprint();anchors={p:verify(p) for p in ROOTS}
    q,a,i=[read_json(Path(p)/name) for p,name in zip(ROOTS,('summary.json','summary.json','receipt.json'))]
    if not all(r['passed'] and r['code_sha256']==fp for r in (q,a,i)) or q['producers']!=producers():
        raise ValueError('qualification/runtime mismatch')
    expected=dict(independent_reference_replays=20,recomputed_comparisons=80,
                  native_integral_energy_checks=40,permutation_pairs=8)
    if a['source_manifest_sha256']!=anchors[ROOTS[0]] or any(a[k]!=v for k,v in expected.items()) or a['maximum_native_integral_error_j']>.1 or a['maximum_reference_integral_error_j']>1e-4 or len(i['rows'])!=84 or not i['reference_imports_blocked']:
        raise ValueError('consumer/replay coverage mismatch')
    inventories=[]
    for p in TESTS:
        tests=list(ET.parse(p).getroot().iter('testcase'))
        if not tests or any(c.find(k) is not None for c in tests for k in ('failure','error','skipped')):
            raise ValueError('nonpassing targeted test evidence')
        inventories.append(dict(path=str(p),count=len(tests),sha256=digest(p.read_bytes())))
    if [r['count'] for r in inventories]!=[153,218]:
        raise ValueError('expected 153 topology/automation and 218 physical regression tests')
    old=Path('reports/network-stator-delivery-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    wheel=Path('reports/topology-stator-wheel-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='7254d8a9316a99a2abacc799d6922be6e34c2f4f846c2fd30305b2b8cc04b062':
        raise ValueError('previous wheel differs')
    with ZipFile(old) as x,ZipFile(wheel) as y:
        before={p:x.read(p) for p in x.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        after={p:y.read(p) for p in y.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed=sorted(p for p in before if before[p]!=after.get(p))
    if changed!=['power_grid_env/automation_jacobian.py'] or before.keys()!=after.keys() or any(Path(p).read_bytes()!=v for p,v in after.items()):
        raise ValueError('runtime source boundary differs')
    probes=[Path('reports/topology-stator-'+name+'-tangent-2026-11-16.json') for name in ('old','fixed')]
    before_probe,after_probe=[read_json(p) for p in probes]
    if before_probe['passed'] or not after_probe['passed'] or before_probe['absolute_error_a_per_k']<.01 or after_probe['absolute_error_a_per_k']>1e-9 or before_probe['study_sha256']!=after_probe['study_sha256'] or before_probe['probe_sha256']!=after_probe['probe_sha256'] or after_probe['code_sha256']!=fp or before_probe['code_sha256']!='8968ab14801d9b0899a46d02ebb746d234903fb78aac50868e201ad6d1eef89f':
        raise ValueError('installed temperature-partial probes differ')
    if after_probe['probe_sha256']!=digest(Path('benchmarks/stator_automation_probe.py').read_bytes()):raise ValueError('probe producer changed')
    if a['native_topology_actions']!={'openings':56,'closings':48,'blocked_closings':8,'dead_receiver_closings':16}:raise ValueError('topology coverage differs')
    cli=Path('reports/topology-stator-cli-2026-11-16.json');trace=read_json(cli)
    if not trace['completed'] or trace['code_sha256']!=fp: raise ValueError('CLI example differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for p in [wheel,*probes,cli,*TESTS]: shutil.copyfile(p,root/p.name)
    (root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name)
    sources={Path(p) for p in producers()}|set(Path('benchmarks').glob('topology_stator*.py'))|set(Path('cases/topology-stator').glob('*'))|{
        Path('tests/test_topology_stator.py'),Path('tests/test_stator_automation_tangent.py'),Path('benchmarks/stator_automation_probe.py'),Path('benchmarks/network_stator_cases.py'),Path('cases/network-stator/relay_first.json'),Path('benchmarks/rms_equilibrium_installed_replay.py'),
        Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml'),Path('README.md'),Path('README.en.md')}
    for p in sources:
        dest=root/'sources'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),
        previous_wheel_sha256=digest(old.read_bytes()),changed_runtime_files=changed,
        evidence_manifests=anchors,test_inventories=inventories,targeted_tests=sum(r['count'] for r in inventories),
        new_topology_and_tangent_tests=42,installed_checks=84,independent_host=False,authenticated=False,
        normative_approval=False,scope=q['scope'],native_topology_actions=a['native_topology_actions'],temperature_partial_error_before_a_per_k=before_probe['absolute_error_a_per_k'],temperature_partial_error_after_a_per_k=after_probe['absolute_error_a_per_k'],**expected)
    (root/'delivery.json').write_bytes(encoded(result))
    if fp!=package_fingerprint() or anchors!={p:verify(p) for p in ROOTS}: raise ValueError('evidence changed')
    seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    print(deliver(**vars(p.parse_args())))
