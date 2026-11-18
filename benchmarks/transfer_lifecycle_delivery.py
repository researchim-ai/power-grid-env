"""Bind supply-PT support, deadline-priority fix and lifecycle qualification."""
import shutil
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.transfer_lifecycle_acceptance import producers

ROOTS = ['reports/transfer-lifecycle-qualified-fixed-2026-11-16',
         'reports/transfer-lifecycle-accepted-2026-11-16',
         'reports/transfer-lifecycle-installed-replay-2026-11-16']
TESTS = [Path('reports/transfer-lifecycle-regression-fixed-2026-11-16.xml'),
         Path('reports/transfer-lifecycle-physical-regression-fixed-2026-11-16.xml')]


def deliver(out):
    fp=package_fingerprint();anchors={p:verify(p) for p in ROOTS}
    q,a,i=[read_json(Path(p)/name) for p,name in zip(ROOTS,('summary.json','summary.json','receipt.json'))]
    if not all(r['passed'] and r['code_sha256']==fp for r in (q,a,i)) or q['producers']!=producers():
        raise ValueError('qualification/runtime mismatch')
    expected=dict(independent_reference_replays=36,recomputed_comparisons=144,
                  native_integral_energy_checks=72,permutation_pairs=16)
    if a['source_manifest_sha256']!=anchors[ROOTS[0]] or any(a[k]!=v for k,v in expected.items()) or a['maximum_native_integral_error_j']>.1 or a['maximum_reference_integral_error_j']>1e-4 or len(i['rows'])!=92 or not i['reference_imports_blocked']:
        raise ValueError('consumer/replay coverage mismatch')
    inventories=[]
    for p in TESTS:
        tests=list(ET.parse(p).getroot().iter('testcase'))
        if not tests or any(c.find(k) is not None for c in tests for k in ('failure','error','skipped')):
            raise ValueError('nonpassing targeted test evidence')
        inventories.append(dict(path=str(p),count=len(tests),sha256=digest(p.read_bytes())))
    if [r['count'] for r in inventories]!=[252,149]:
        raise ValueError('expected measured-workflow and physical regression inventories')
    old=Path('reports/measurement-transfer-delivery-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    wheel=Path('reports/transfer-lifecycle-wheel-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='02859cb2098776cacd76a32585ba41f3be3591d6fe32d4798ba36ecc4245dccd':raise ValueError('previous wheel differs')
    with ZipFile(old) as x,ZipFile(wheel) as y:
        before={p:x.read(p) for p in x.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        after={p:y.read(p) for p in y.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed=sorted(p for p in before if before[p]!=after.get(p))
    expected_runtime=['power_grid_env/automation_jacobian.py','power_grid_env/coupled_initialization.py','power_grid_env/coupled_rms.py','power_grid_env/dae.py','power_grid_env/induction_network.py','power_grid_env/network_automation.py']
    if changed!=expected_runtime or before.keys()!=after.keys() or any(Path(p).read_bytes()!=v for p,v in after.items()):raise ValueError('runtime source boundary differs')
    probes=[Path('reports/transfer-lifecycle-'+name+'-deadline-2026-11-16.json') for name in ('old','fixed')]
    old_probe,new_probe=[read_json(p) for p in probes]
    if old_probe['passed'] or not new_probe['passed'] or [r['passed'] for r in old_probe['rows']]!=[False,True] or [r['passed'] for r in new_probe['rows']]!=[True,True] or old_probe['study_sha256']!=new_probe['study_sha256'] or old_probe['probe_sha256']!=new_probe['probe_sha256'] or new_probe['code_sha256']!=fp:raise ValueError('deadline priority probes differ')
    if new_probe['probe_sha256']!=digest(Path('benchmarks/transfer_deadline_probe.py').read_bytes()):raise ValueError('probe changed')
    actions={'transfer_starts':96,'early_blocks':24,'reserve_closes':32,'close_blocks':64,'grid_returns':24,'relay_trips':8,'pt_losses':40}
    if a['native_protection_actions']!=actions or a['reference_clock_segments_checked']!=448 or a['maximum_clock_integral_error']>2e-8:raise ValueError('lifecycle/clock coverage differs')
    cli=Path('reports/transfer-lifecycle-cli-2026-11-16.json');trace=read_json(cli)
    if not trace['completed'] or trace['code_sha256']!=fp: raise ValueError('CLI example differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for p in [wheel,cli,*probes,*TESTS]: shutil.copyfile(p,root/p.name)
    (root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name)
    sources={Path(p) for p in producers()}|set(Path('benchmarks').glob('transfer_lifecycle*.py'))|set(Path('cases/transfer-lifecycle').glob('*'))|{
        Path('tests/test_transfer_lifecycle.py'),Path('benchmarks/transfer_deadline_probe.py'),Path('benchmarks/transfer_lifecycle_installed.py'),Path('benchmarks/network_stator_cases.py'),Path('cases/network-stator/relay_first.json'),Path('benchmarks/rms_equilibrium_installed_replay.py'),
        Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml'),Path('README.md'),Path('README.en.md')}
    for p in sources:
        dest=root/'sources'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),
        previous_wheel_sha256=digest(old.read_bytes()),changed_runtime_files=changed,
        evidence_manifests=anchors,test_inventories=inventories,targeted_tests=sum(r['count'] for r in inventories),
        new_lifecycle_tests=39,installed_checks=92,independent_host=False,authenticated=False,
        normative_approval=False,scope=q['scope'],native_protection_actions=actions,reference_clock_segments_checked=a['reference_clock_segments_checked'],**expected)
    (root/'delivery.json').write_bytes(encoded(result))
    if fp!=package_fingerprint() or anchors!={p:verify(p) for p in ROOTS}: raise ValueError('evidence changed')
    seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    print(deliver(**vars(p.parse_args())))
