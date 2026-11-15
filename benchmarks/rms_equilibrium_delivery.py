"""Local R06.39 delivery: stationary roots,watts,spectra and pinned wheel replay."""
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from benchmarks.rms_equilibrium_acceptance import producers
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest

ROOTS=['reports/rms-equilibrium-power-qualified-2026-11-15','reports/rms-equilibrium-power-accepted-2026-11-15',
       'reports/rms-equilibrium-power-installed-replay-2026-11-15','reports/rms-equilibrium-cli-packet-2026-11-15']
TESTS='reports/rms-equilibrium-final-regression-tests-2026-11-15.xml'


def deliver(out):
    anchors={p:verify(p) for p in ROOTS};fp=package_fingerprint()
    source=read_json(Path(ROOTS[0])/'summary.json');accepted=read_json(Path(ROOTS[1])/'summary.json');installed=read_json(Path(ROOTS[2])/'receipt.json')
    if not source['passed'] or source['code_sha256']!=fp or source['producers']!=producers() or not accepted['passed'] or accepted['source_manifest_sha256']!=anchors[ROOTS[0]] or accepted['code_sha256']!=fp or accepted['independent_reference_replays']!=80 or accepted['native_initial_rejection_replays']!=12 or not installed['passed'] or installed['code_sha256']!=fp or len(installed['rows'])!=8:raise ValueError('stationary qualification/installed binding differs')
    counts={k:0 for k in ['tests','errors','failures','skipped']}
    for suite in ET.parse(TESTS).getroot().findall('testsuite'):
        for key in counts:counts[key]+=int(suite.get(key,'0'))
    if counts['tests']!=791 or any(counts[k] for k in ['errors','failures','skipped']):raise ValueError('expected791 passing targeted tests')
    old=Path('reports/rms-winding-delivery-2026-11-15/power_grid_env-0.1.0-py3-none-any.whl')
    wheel=Path('reports/rms-equilibrium-power-wheel-2026-11-15/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='b74b35cf6b09dfebfbe0a715229d523c4ebc5c0fd5e47c04146f107c7baab1eb':raise ValueError('previous qualified wheel differs')
    with ZipFile(old) as a,ZipFile(wheel) as b:
        before={p:a.read(p) for p in a.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        after={p:b.read(p) for p in b.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed=sorted(p for p in before.keys()&after.keys() if before[p]!=after[p])
    expected=['coupled_initialization','induction','induction_network','motor_operating_point']
    if changed!=sorted('power_grid_env/'+p+'.py' for p in expected) or after.keys()-before.keys()!={'power_grid_env/rms_winding_equilibrium.py'} or before.keys()-after.keys() or any(Path(p).read_bytes()!=v for p,v in after.items()):raise ValueError('runtime/source/wheel boundary differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(wheel,root/wheel.name);(root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name)
    shutil.copyfile(TESTS,root/'tests.xml')
    files=set(Path(p) for p in producers())|set(Path('benchmarks').glob('rms_equilibrium*.py'))|{Path('tests/test_rms_equilibrium.py')}|set(Path('cases/rms-equilibrium').glob('*'))
    for file in sorted(files):
        target=root/'sources'/file;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target)
    proof=dict(changed_runtime_files=changed,added_runtime_files=['power_grid_env/rms_winding_equilibrium.py'],missing_runtime_files=[],old_wheel_sha256=digest(old.read_bytes()),new_wheel_sha256=digest(wheel.read_bytes()))
    (root/'source-boundary.json').write_bytes(encoded(proof))
    receipt=dict(passed=True,code_sha256=fp,wheel_sha256=proof['new_wheel_sha256'],evidence_manifests=anchors,tests=counts,optional_ANDES_deselected=4,
        main_native_launches=156,completed_main_native_runs=144,initialization_rejections=12,independent_trajectories=80,
        independent_reference_replays=80,native_rejection_replays=12,installed_replays=8,installed_expected_refusals=2,
        maximum_stationary_power_error_w=accepted['maximum_stationary_power_error_w'],independent_host=False,authenticated=False,normative_approval=False,
        scope='R06.39 bounded connected motor-only single-cage joint electrothermal equilibrium.Heat-power residual prevents huge-C false equilibrium;full physical spectrum excludes hybrid stability.791 targeted checks,not whole repo suite.8 installed same-host checks include4 new completed,2 refusals,2 legacy cases.Prior corpora retain old wheels/fingerprints.No OEM/CI/publication/global-superiority claim.')
    (root/'delivery.json').write_bytes(encoded(receipt))
    if {p:verify(p) for p in ROOTS}!=anchors or package_fingerprint()!=fp:raise ValueError('evidence/runtime changed during delivery')
    seal(root);return receipt


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True)
    r=deliver(**vars(p.parse_args()));print(dict(passed=r['passed'],tests=r['tests']['tests']))
