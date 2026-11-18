"""Bind standby/measurement qualification to the unchanged R06.55 runtime."""
import shutil
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from power_grid_env.study_artifacts import read_json, encoded, seal, verify, digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.measurement_transfer_acceptance import producers

ROOTS = ['reports/measurement-transfer-qualified-2026-11-16',
         'reports/measurement-transfer-accepted-2026-11-16',
         'reports/measurement-transfer-installed-replay-2026-11-16']
TESTS = [Path('reports/measurement-transfer-regression-2026-11-16.xml'),
         Path('reports/measurement-transfer-physical-regression-2026-11-16.xml')]


def deliver(out):
    fp=package_fingerprint();anchors={p:verify(p) for p in ROOTS}
    q,a,i=[read_json(Path(p)/name) for p,name in zip(ROOTS,('summary.json','summary.json','receipt.json'))]
    if not all(r['passed'] and r['code_sha256']==fp for r in (q,a,i)) or q['producers']!=producers():
        raise ValueError('qualification/runtime mismatch')
    expected=dict(independent_reference_replays=44,recomputed_comparisons=176,
                  native_integral_energy_checks=88,permutation_pairs=16)
    if a['source_manifest_sha256']!=anchors[ROOTS[0]] or any(a[k]!=v for k,v in expected.items()) or a['maximum_native_integral_error_j']>.1 or a['maximum_reference_integral_error_j']>1e-4 or len(i['rows'])!=108 or not i['reference_imports_blocked']:
        raise ValueError('consumer/replay coverage mismatch')
    inventories=[]
    for p in TESTS:
        tests=list(ET.parse(p).getroot().iter('testcase'))
        if not tests or any(c.find(k) is not None for c in tests for k in ('failure','error','skipped')):
            raise ValueError('nonpassing targeted test evidence')
        inventories.append(dict(path=str(p),count=len(tests),sha256=digest(p.read_bytes())))
    if [r['count'] for r in inventories]!=[247,171]:
        raise ValueError('expected measured-workflow and physical regression inventories')
    wheel=Path('reports/protected-topology-delivery-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(wheel.read_bytes())!='02859cb2098776cacd76a32585ba41f3be3591d6fe32d4798ba36ecc4245dccd':raise ValueError('reused R06.55 wheel differs')
    with ZipFile(wheel) as z:
        runtime={p:z.read(p) for p in z.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    if any(Path(p).read_bytes()!=v for p,v in runtime.items()):raise ValueError('runtime source changed since R06.55')
    actions={'measurement_losses':40,'measurement_restores':32,'relay_trips':56,'transfer_starts':40,'transfer_blocks':16,'reserve_closes':16,'reserve_close_blocks':8}
    if a['native_protection_actions']!=actions or a['reference_clock_segments_checked']==0 or a['maximum_clock_integral_error']>2e-8:raise ValueError('measurement/transfer clock coverage differs')
    cli=Path('reports/measurement-transfer-cli-2026-11-16.json');trace=read_json(cli)
    if not trace['completed'] or trace['code_sha256']!=fp: raise ValueError('CLI example differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for p in [wheel,cli,*TESTS]: shutil.copyfile(p,root/p.name)
    sources={Path(p) for p in producers()}|set(Path('benchmarks').glob('measurement_transfer*.py'))|set(Path('cases/measurement-transfer').glob('*'))|{
        Path('tests/test_measurement_transfer.py'),Path('benchmarks/measurement_transfer_installed.py'),Path('benchmarks/network_stator_cases.py'),Path('cases/network-stator/relay_first.json'),Path('benchmarks/rms_equilibrium_installed_replay.py'),
        Path('plans/CURRENT_STATUS.md'),Path('plans/agent-designer.yaml'),Path('README.md'),Path('README.en.md')}
    for p in sources:
        dest=root/'sources'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),
        runtime_reused_from='R06.55',changed_runtime_files=[],
        evidence_manifests=anchors,test_inventories=inventories,targeted_tests=sum(r['count'] for r in inventories),
        new_measurement_transfer_tests=50,installed_checks=108,independent_host=False,authenticated=False,
        normative_approval=False,scope=q['scope'],native_protection_actions=actions,reference_clock_segments_checked=a['reference_clock_segments_checked'],**expected)
    (root/'delivery.json').write_bytes(encoded(result))
    if fp!=package_fingerprint() or anchors!={p:verify(p) for p in ROOTS}: raise ValueError('evidence changed')
    seal(root)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    print(deliver(**vars(p.parse_args())))
