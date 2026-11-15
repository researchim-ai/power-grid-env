"""Local RMS winding release receipt; prior wheels/evidence keep their identity."""
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from power_grid_env.study_artifacts import read_json,encoded,digest,verify,seal
from power_grid_env.evidence import package_fingerprint
from benchmarks.rms_winding_acceptance import producers

ROOTS=['reports/rms-winding-final-qualified-2026-11-15','reports/rms-winding-final-accepted-2026-11-15',
       'reports/rms-winding-final-installed-replay-2026-11-15','reports/rms-winding-final-cli-packet-2026-11-15']
BROAD='reports/rms-winding-final-regression-tests-2026-11-15.xml'
RERUN='reports/rms-winding-validity-tests-2026-11-15.xml'


def tests_receipt():
    """Keep raw XML; replace only the explicitly rerun test class outcomes.

    The broad run used an incorrect assertion about the existing failure
    envelope. Both validity cases now require the exact model-validity cause
    in the numerical-failure envelope; the full22-case class was rerun.
    No production code changed between these two final-runtime test runs.
    """
    def cases(file):
        result={}
        for case in ET.parse(file).getroot().iter('testcase'):
            key=(case.get('classname'),case.get('name'))
            if key in result:raise ValueError('duplicate JUnit node ID')
            result[key]=dict(passed=case.find('failure') is None and case.find('error') is None and case.find('skipped') is None)
        return result
    broad,rerun=cases(BROAD),cases(RERUN)
    expected={k for k in broad if k[0]=='tests.test_rms_winding'}
    if set(rerun)!=expected or len(rerun)!=22 or any(not row['passed'] for row in rerun.values()):raise ValueError('complete22-case RMS rerun required')
    if any(not row['passed'] for k,row in broad.items() if k not in rerun):raise ValueError('non-rerun regression failure')
    merged=dict(broad);merged.update(rerun)
    if len(merged)!=769 or not all(row['passed'] for row in merged.values()):raise ValueError('expected769 unique final passing checks')
    return dict(unique_tests=769,final_passed=769,final_failed=0,final_skipped=0,
        broad_xml_tests=len(broad),broad_xml_failed=sum(not r['passed'] for r in broad.values()),
        rerun_tests=22,superseded_class='tests.test_rms_winding',
        note='Raw broad XML retains2 obsolete status-envelope assertions; final22-case class rerun passed after correcting the test contract and checking exact model-validity cause.747 other final-runtime cases passed in broad run.Production code unchanged between final broad and rerun.')


def deliver(out):
    fp=package_fingerprint();anchors={p:verify(p) for p in ROOTS}
    source=read_json(Path(ROOTS[0])/'summary.json');accepted=read_json(Path(ROOTS[1])/'summary.json');installed=read_json(Path(ROOTS[2])/'receipt.json')
    if not source['passed'] or source['code_sha256']!=fp or source['producers']!=producers() or not accepted['passed'] or accepted['source_manifest_sha256']!=anchors[ROOTS[0]] or accepted['code_sha256']!=fp or accepted['independent_reference_replays']!=64 or not installed['passed'] or installed['code_sha256']!=fp or len(installed['rows'])!=8:raise ValueError('qualification/acceptance/installed binding differs')
    tests=tests_receipt()
    old=Path('reports/electrothermal-fault-delivery-2026-11-15/power_grid_env-0.1.0-py3-none-any.whl')
    wheel=Path('reports/rms-winding-final-wheel-2026-11-15/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='62500d8cd7fd5ae7efe80ad5515b99c950b616ac10f0000a79c3a2e59c7ef434':raise ValueError('previous qualified wheel differs')
    with ZipFile(old) as a,ZipFile(wheel) as b:
        before={n:a.read(n) for n in a.namelist() if n.startswith('power_grid_env/') and n.endswith('.py')}
        after={n:b.read(n) for n in b.namelist() if n.startswith('power_grid_env/') and n.endswith('.py')}
    changed=sorted(n for n in before.keys()&after.keys() if before[n]!=after[n])
    expected=['automation_jacobian','coupled_initialization','coupled_rms','dae','dae_linearization','dae_solver','induction','induction_network','motor_operating_point','network_automation']
    if changed!=sorted('power_grid_env/'+p+'.py' for p in expected) or after.keys()-before.keys()!={'power_grid_env/rms_winding.py'} or before.keys()-after.keys() or any(Path(n).read_bytes()!=v for n,v in after.items()):raise ValueError('runtime source/wheel boundary differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);shutil.copyfile(wheel,root/wheel.name)
    (root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name)
    for file in [BROAD,RERUN]:shutil.copyfile(file,root/Path(file).name)
    files=list(Path('benchmarks').glob('rms_winding*.py'))+[Path('benchmarks/electrothermal_installed_replay.py'),Path('benchmarks/loaded_protection_winding_acceptance.py'),Path('benchmarks/motor_only_loaded_reference.py'),Path('tests/test_rms_winding.py')]
    files+=list(Path('cases/rms-winding').glob('*'))
    for file in files:
        target=root/'sources'/file;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target)
    proof=dict(changed_runtime_files=changed,added_runtime_files=['power_grid_env/rms_winding.py'],missing_runtime_files=[],
               old_wheel_sha256=digest(old.read_bytes()),new_wheel_sha256=digest(wheel.read_bytes()))
    (root/'source-boundary.json').write_bytes(encoded(proof))
    result=dict(passed=True,code_sha256=fp,wheel_sha256=proof['new_wheel_sha256'],evidence_manifests=anchors,tests=tests,
        optional_ANDES_deselected=4,native_runs=128,independent_trajectories=64,independent_reference_replays=64,installed_replays=8,
        independent_host=False,authenticated=False,normative_approval=False,
        scope='R06.38 opt-in motor-only single-cage RMS R(T),state-dependent analytic DAE,active protection and frozen-initial-R controls.Bounded authored radial corpus,not full thermal equilibrium/OEM/global topology approval.769 unique checks pass after explicit22-case test-contract rerun;raw XML retained.8 installed same-host replays.No CI/publication or universal superiority claim.')
    (root/'delivery.json').write_bytes(encoded(result))
    if {p:verify(p) for p in ROOTS}!=anchors or package_fingerprint()!=fp:raise ValueError('evidence/runtime changed during delivery')
    seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True)
    r=deliver(**vars(p.parse_args()));print(dict(passed=r['passed'],unique_tests=r['tests']['unique_tests']))
