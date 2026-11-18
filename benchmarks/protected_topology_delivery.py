"""Bind the stable inverse curve fix, active protection and bounded wheel replay."""
import shutil
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.protected_topology_acceptance import producers

ROOTS = ['reports/protected-topology-qualified-compact-2026-11-16',
         'reports/protected-topology-accepted-2026-11-16',
         'reports/protected-topology-installed-replay-2026-11-16']
TESTS = [Path('reports/protected-topology-regression-2026-11-16.xml'),
         Path('reports/protected-topology-physical-regression-2026-11-16.xml')]


def deliver(out):
    fp=package_fingerprint();anchors={p:verify(p) for p in ROOTS}
    q,a,i=[read_json(Path(p)/name) for p,name in zip(ROOTS,('summary.json','summary.json','receipt.json'))]
    if not all(r['passed'] and r['code_sha256']==fp for r in (q,a,i)) or q['producers']!=producers():
        raise ValueError('qualification/runtime mismatch')
    expected=dict(independent_reference_replays=36,recomputed_comparisons=144,
                  native_integral_energy_checks=72,permutation_pairs=8)
    if a['source_manifest_sha256']!=anchors[ROOTS[0]] or any(a[k]!=v for k,v in expected.items()) or a['maximum_native_integral_error_j']>.1 or a['maximum_reference_integral_error_j']>1e-4 or len(i['rows'])!=92 or not i['reference_imports_blocked']:
        raise ValueError('consumer/replay coverage mismatch')
    inventories=[]
    for p in TESTS:
        tests=list(ET.parse(p).getroot().iter('testcase'))
        if not tests or any(c.find(k) is not None for c in tests for k in ('failure','error','skipped')):
            raise ValueError('nonpassing targeted test evidence')
        inventories.append(dict(path=str(p),count=len(tests),sha256=digest(p.read_bytes())))
    if [r['count'] for r in inventories]!=[296,257]:
        raise ValueError('expected 296 protection/workflow and 257 physical regression tests')
    old=Path('reports/topology-stator-delivery-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    wheel=Path('reports/protected-topology-wheel-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='72f7371d77513023aeb2dcc2980fdd180c3e65a9d5eb00b97db7d064308b3b3c':
        raise ValueError('previous wheel differs')
    with ZipFile(old) as x,ZipFile(wheel) as y:
        before={p:x.read(p) for p in x.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        after={p:y.read(p) for p in y.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed=sorted(p for p in before if before[p]!=after.get(p))
    if changed!=['power_grid_env/protection.py'] or before.keys()!=after.keys() or any(Path(p).read_bytes()!=v for p,v in after.items()):
        raise ValueError('runtime source boundary differs')
    probes=[Path('reports/protected-topology-'+name+'-pickup-2026-11-16.json') for name in ('old','fixed')]
    before_probe,after_probe=[read_json(p) for p in probes]
    if before_probe['passed'] or not after_probe['passed'] or before_probe['failed_checks']!=7 or after_probe['failed_checks']!=0 or len(after_probe['rows'])!=9 or before_probe['probe_sha256']!=after_probe['probe_sha256'] or after_probe['code_sha256']!=fp or before_probe['code_sha256']!='d947dde8a0c9283d7094f2fcb6014c8f3d0d8121d69991b2ca6771a5dbbfdbd6':
        raise ValueError('installed pickup probes differ')
    if after_probe['probe_sha256']!=digest(Path('benchmarks/inverse_pickup_probe.py').read_bytes()):raise ValueError('probe producer changed')
    actions={'relay_trips':112,'primary_applied':104,'primary_failed':8,'reclose_applied':40,'reclose_blocked':16,'backup_applied':8}
    if a['native_protection_actions']!=actions or a['reference_clock_segments_checked']==0 or a['maximum_clock_integral_error']>2e-8:raise ValueError('protection/clock coverage differs')
    cli=Path('reports/protected-topology-cli-2026-11-16.json');trace=read_json(cli)
    if not trace['completed'] or trace['code_sha256']!=fp: raise ValueError('CLI example differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for p in [wheel,*probes,cli,*TESTS]: shutil.copyfile(p,root/p.name)
    (root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name)
    sources={Path(p) for p in producers()}|set(Path('benchmarks').glob('protected_topology*.py'))|set(Path('cases/protected-topology').glob('*'))|{
        Path('tests/test_protected_topology.py'),Path('benchmarks/protected_topology_installed.py'),Path('benchmarks/inverse_pickup_probe.py'),Path('benchmarks/network_stator_cases.py'),Path('cases/network-stator/relay_first.json'),Path('benchmarks/rms_equilibrium_installed_replay.py'),
        Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml'),Path('README.md'),Path('README.en.md')}
    for p in sources:
        dest=root/'sources'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),
        previous_wheel_sha256=digest(old.read_bytes()),changed_runtime_files=changed,
        evidence_manifests=anchors,test_inventories=inventories,targeted_tests=sum(r['count'] for r in inventories),
        new_protection_tests=45,installed_checks=92,independent_host=False,authenticated=False,
        normative_approval=False,scope=q['scope'],native_protection_actions=actions,reference_clock_segments_checked=a['reference_clock_segments_checked'],pickup_probe_checks=9,old_pickup_probe_failures=7,**expected)
    (root/'delivery.json').write_bytes(encoded(result))
    if fp!=package_fingerprint() or anchors!={p:verify(p) for p in ROOTS}: raise ValueError('evidence changed')
    seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    print(deliver(**vars(p.parse_args())))
