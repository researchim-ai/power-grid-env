"""Bind the one-file clock fix, independent evidence and installed local replay."""
import shutil
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.network_stator_acceptance import producers

ROOTS = ['reports/network-stator-qualified-fixed-2026-11-16',
         'reports/network-stator-accepted-2026-11-16',
         'reports/network-stator-installed-replay-2026-11-16']
TESTS = [Path('reports/network-stator-full-regression-2026-11-16.xml'),
         Path('reports/network-stator-automation-regression-2026-11-16.xml')]


def deliver(out):
    fp=package_fingerprint();anchors={p:verify(p) for p in ROOTS}
    q,a,i=[read_json(Path(p)/name) for p,name in zip(ROOTS,('summary.json','summary.json','receipt.json'))]
    if not all(r['passed'] and r['code_sha256']==fp for r in (q,a,i)) or q['producers']!=producers():
        raise ValueError('qualification/runtime mismatch')
    expected=dict(independent_reference_replays=16,recomputed_comparisons=64,
                  native_integral_energy_checks=32,native_thermal_trips=24,native_failure_confirmations=8,permutation_pairs=8)
    if a['source_manifest_sha256']!=anchors[ROOTS[0]] or any(a[k]!=v for k,v in expected.items()) or a['maximum_native_integral_error_j']>.1 or a['maximum_reference_integral_error_j']>1e-4 or len(i['rows'])!=44 or not i['reference_imports_blocked']:
        raise ValueError('consumer/replay coverage mismatch')
    inventories=[]
    for p in TESTS:
        tests=list(ET.parse(p).getroot().iter('testcase'))
        if not tests or any(c.find(k) is not None for c in tests for k in ('failure','error','skipped')):
            raise ValueError('nonpassing targeted test evidence')
        inventories.append(dict(path=str(p),count=len(tests),sha256=digest(p.read_bytes())))
    if [r['count'] for r in inventories]!=[418,167]:
        raise ValueError('expected 418 physics/DAE and 167 automation/protection tests')
    old=Path('reports/hybrid-stator-delivery-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    wheel=Path('reports/network-stator-wheel-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='358ae50f48724687c0863c5e6cae196736e76845ab787cea7a6acc95f1f7c564':
        raise ValueError('previous wheel differs')
    with ZipFile(old) as x,ZipFile(wheel) as y:
        before={p:x.read(p) for p in x.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        after={p:y.read(p) for p in y.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed=sorted(p for p in before if before[p]!=after.get(p))
    if changed!=['power_grid_env/network_automation.py'] or before.keys()!=after.keys() or any(Path(p).read_bytes()!=v for p,v in after.items()):
        raise ValueError('runtime source boundary differs')
    pre=Path('reports/network-stator-pre-fix-2026-11-16.json');reproducer=read_json(pre)
    if reproducer['code_sha256']!='bce87394ab8ec55f6f161157bea5cf994b4d1a2c61533bf3de84a3fe24d41a2b' or reproducer['expected_failures']!=4 or len(reproducer['rows'])!=4 or any(r['report']['completed'] or r['report']['status']!='numerically_not_converged' for r in reproducer['rows']):
        raise ValueError('pre-fix reproducer differs')
    cli=Path('reports/network-stator-cli-2026-11-16.json');trace=read_json(cli)
    if not trace['completed'] or trace['code_sha256']!=fp: raise ValueError('CLI example differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for p in [wheel,pre,cli,*TESTS]: shutil.copyfile(p,root/p.name)
    (root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name)
    sources={Path(p) for p in producers()}|set(Path('benchmarks').glob('network_stator*.py'))|set(Path('cases/network-stator').glob('*'))|{
        Path('tests/test_network_stator.py'),Path('benchmarks/rms_equilibrium_installed_replay.py'),
        Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml'),Path('README.md'),Path('README.en.md')}
    for p in sources:
        dest=root/'sources'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),
        previous_wheel_sha256=digest(old.read_bytes()),changed_runtime_files=changed,
        evidence_manifests=anchors,test_inventories=inventories,targeted_tests=sum(r['count'] for r in inventories),
        new_network_stator_tests=27,installed_checks=44,independent_host=False,authenticated=False,
        normative_approval=False,scope=q['scope'],**expected)
    (root/'delivery.json').write_bytes(encoded(result))
    if fp!=package_fingerprint() or anchors!={p:verify(p) for p in ROOTS}: raise ValueError('evidence changed')
    seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    print(deliver(**vars(p.parse_args())))
