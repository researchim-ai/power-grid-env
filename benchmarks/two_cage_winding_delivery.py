"""Local two-cage electrothermal delivery and explicit workflow postconditions."""
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile
from benchmarks.two_cage_winding_acceptance import producers
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest

ROOTS=['reports/two-cage-winding-final-qualified-2026-11-15','reports/two-cage-winding-accepted-2026-11-15',
       'reports/two-cage-winding-installed-replay-2026-11-15','reports/two-cage-winding-cli-packet-2026-11-15']
TESTS='reports/two-cage-winding-regression-tests-2026-11-15.xml'


def deliver(out):
    anchors={p:verify(p) for p in ROOTS};fp=package_fingerprint();source=read_json(Path(ROOTS[0])/'summary.json');accepted=read_json(Path(ROOTS[1])/'summary.json');installed=read_json(Path(ROOTS[2])/'receipt.json')
    if not source['passed'] or source['code_sha256']!=fp or source['producers']!=producers() or not accepted['passed'] or accepted['source_manifest_sha256']!=anchors[ROOTS[0]] or accepted['code_sha256']!=fp or accepted['independent_reference_replays']!=66 or accepted['native_refusal_replays']!=12 or not installed['passed'] or installed['code_sha256']!=fp or len(installed['rows'])!=8:raise ValueError('two-cage producer/acceptance/installed binding differs')
    postconditions=dict(protected_motor_open=0,hold_motor_connected=0,no_uv_motor_connected=0)
    for case in source['rows']:
        if case['negative']:continue
        for row in case['rows']:
            report=read_json(Path(ROOTS[0])/case['id']/(row['method']+'-'+str(row['grid']))/'trace.json')
            opened=report['channels']['m.connected'][-1]==0.;expected=case['kind'] not in ['hold','primary-no-uv']
            if opened!=expected:raise ValueError('declared motor-disconnection postcondition failed')
            if expected:
                if not any(e['kind']=='motor_open' and e.get('cause')=='undervoltage_timer' for e in report['event_log']):raise ValueError('declared UV opening absent')
                postconditions['protected_motor_open']+=1
            else:postconditions['no_uv_motor_connected' if case['kind']=='primary-no-uv' else 'hold_motor_connected']+=1
    if postconditions!=dict(protected_motor_open=84,hold_motor_connected=12,no_uv_motor_connected=24):raise ValueError('workflow coverage differs')
    counts={k:0 for k in ['tests','errors','failures','skipped']}
    for suite in ET.parse(TESTS).getroot().findall('testsuite'):
        for key in counts:counts[key]+=int(suite.get(key,'0'))
    if counts['tests']!=841 or any(counts[k] for k in ['errors','failures','skipped']):raise ValueError('expected841 passing targeted tests')
    old=Path('reports/rms-equilibrium-delivery-2026-11-15/power_grid_env-0.1.0-py3-none-any.whl');wheel=Path('reports/two-cage-winding-wheel-2026-11-15/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='cb8ce943ec9f5be76b71f5dea5815bafaf1a9f6300de51fba3ab61ea0b1660c2':raise ValueError('old qualified wheel differs')
    with ZipFile(old) as a,ZipFile(wheel) as b:
        before={p:a.read(p) for p in a.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')};after={p:b.read(p) for p in b.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed=sorted(p for p in before.keys()&after.keys() if before[p]!=after[p])
    expected=['induction','induction_network','coupled_rms','rms_winding','rms_winding_equilibrium','motor_transfer']
    if changed!=sorted('power_grid_env/'+p+'.py' for p in expected) or before.keys()!=after.keys() or any(Path(p).read_bytes()!=v for p,v in after.items()):raise ValueError('runtime/source/wheel boundary differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);shutil.copyfile(wheel,root/wheel.name);(root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name);shutil.copyfile(TESTS,root/'tests.xml')
    files=set(Path(p) for p in producers())|set(Path('benchmarks').glob('two_cage_winding*.py'))|{Path('benchmarks/rms_equilibrium_installed_replay.py'),Path('tests/test_two_cage_winding.py'),Path('tests/test_rms_winding.py'),Path('tests/test_motor_transfer.py')}|set(Path('cases/two-cage-winding').glob('*'))
    for file in sorted(files):
        target=root/'sources'/file;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target)
    (root/'source-boundary.json').write_bytes(encoded(dict(changed_runtime_files=changed,added_runtime_files=[],missing_runtime_files=[],old_wheel_sha256=digest(old.read_bytes()),new_wheel_sha256=digest(wheel.read_bytes()))))
    receipt=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),evidence_manifests=anchors,tests=counts,optional_ANDES_deselected=4,
        completed_native_runs=120,initialization_rejections=12,independent_trajectories=66,independent_reference_replays=66,native_refusal_replays=12,
        installed_checks=8,workflow_postconditions=postconditions,independent_host=False,authenticated=False,normative_approval=False,
        scope='R06.40 two-cage RMS R(T),common rotor temperature,individual cage losses,analytic DAE and full stationary physical spectrum.33 authored radial scenarios,not3+ cages/mixed generators/separate cage hotspots.841 targeted tests,not full repo suite.8 installed same-host checks;prior evidence retains old wheels.Frozen no-UV controls are numerical studies,not engineering approvals.No CI/publication/universal-superiority claim.')
    (root/'delivery.json').write_bytes(encoded(receipt))
    if {p:verify(p) for p in ROOTS}!=anchors or package_fingerprint()!=fp:raise ValueError('evidence/runtime changed during delivery')
    seal(root);return receipt


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True)
    r=deliver(**vars(p.parse_args()));print(dict(passed=r['passed'],tests=r['tests']['tests']))
