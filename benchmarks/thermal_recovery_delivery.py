"""Bind thermal recovery, diagnostic curriculum, wheel and regression evidence."""
import shutil
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from power_grid_env.study_artifacts import encoded,read_json,digest,seal,verify
from power_grid_env.evidence import package_fingerprint
from benchmarks.thermal_recovery_benchmark import producers

ROOTS=['reports/thermal-recovery-'+name+'-2026-11-16' for name in ('qualified','accepted','installed-replay','learning','learning-installed')]
TESTS=[Path('reports/thermal-recovery-'+name+'-2026-11-16.xml') for name in ('regression','physical-regression','thermal-regression-final')]


def deliver(out):
    fp=package_fingerprint();anchors={p:verify(p) for p in ROOTS}
    q,a,i,l,w=[read_json(Path(p)/('summary.json' if j<2 else 'receipt.json')) for j,p in enumerate(ROOTS)]
    if not all(r['passed'] and r['code_sha256']==fp for r in (q,a,i,l,w)) or q['producers']!=producers() or a['producers']!=producers():raise ValueError('evidence/runtime differs')
    expected=dict(thermal_trips=144,reset_applied=64,reset_blocked=16,reset_noop=8,close_applied=144,close_blocked=48,transfer_starts=104)
    if (q['physical_cases'],q['native_runs'],q['independent_trajectories'],q['comparisons'])!=(20,80,40,160) or a['source_manifest_sha256']!=anchors[ROOTS[0]] or a['permutation_pairs']!=16 or a['actions']!=expected or a['reference_clock_segments']!=1024 or a['maximum_native_energy_error_j']>.1 or a['maximum_reference_energy_error_j']>1e-4 or a['maximum_clock_error']>2e-8:raise ValueError('physical qualification inventory differs')
    if len(i['rows'])!=96 or not i['reference_imports_blocked'] or not all(r['passed'] and r['compared'] and r['common_channels_bitwise_exact'] for r in i['rows']):raise ValueError('installed simulation differs')
    for r in (l,w):
        if r['train_episodes']!=18 or r['families']!=1 or r['split']!='train' or r['model_trained'] or r['VLM_images'] or not all(x['passed'] and (x['positive_reward'],x['wrong_reward'],x['malformed_reward'])==(1.,0.,0.) for x in r['rows']):raise ValueError('learning scope/inventory differs')
    if not w['reference_imports_blocked'] or any(l[k]!=w[k] for k in ('sft_sha256','prompts_sha256','corpus_manifest_sha256')):raise ValueError('installed diagnostic export differs')
    inventories=[]
    for path in TESTS:
        tests=list(ET.parse(path).getroot().iter('testcase'))
        if not tests or any(c.find(k) is not None for c in tests for k in ('failure','error','skipped')):raise ValueError('nonpassing regression evidence')
        inventories.append(dict(path=str(path),tests=len(tests),sha256=digest(path.read_bytes())))
    if [r['tests'] for r in inventories]!=[319,149,52]:raise ValueError('targeted regression inventory differs')
    old=Path('reports/standby-interlocks-delivery-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    wheel=Path('reports/thermal-recovery-wheel-2026-11-16/power_grid_env-0.1.0-py3-none-any.whl')
    if digest(old.read_bytes())!='19fc0ea697b7e3c5c0daeae51251868ac2d4e12f12adfaebf479bc2a3f7958a0':raise ValueError('previous wheel differs')
    with ZipFile(old) as x,ZipFile(wheel) as y:
        before={p:x.read(p) for p in x.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
        after={p:y.read(p) for p in y.namelist() if p.startswith('power_grid_env/') and p.endswith('.py')}
    changed=sorted(p for p in before if before[p]!=after.get(p));added=sorted(after.keys()-before.keys())
    if changed!=['power_grid_env/coupled_rms.py','power_grid_env/dae.py','power_grid_env/generator_thermal.py','power_grid_env/network_automation.py'] or added!=['power_grid_env/recovery_curriculum.py'] or before.keys()-after.keys() or any(Path(p).read_bytes()!=v for p,v in after.items()):raise ValueError('runtime boundary differs')
    cli=Path('reports/thermal-recovery-cli-2026-11-16.json');trace=read_json(cli)
    if not trace['completed'] or trace['code_sha256']!=fp or len([e for e in trace['event_log'] if e['kind']=='generator_thermal_trip'])!=2:raise ValueError('CLI retrip differs')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for p in [wheel,cli,*TESTS]:shutil.copyfile(p,root/p.name)
    (root/'old-runtime').mkdir();shutil.copyfile(old,root/'old-runtime'/old.name)
    sources=({Path(p) for p in producers()}|set(Path('benchmarks').glob('thermal_recovery*.py'))|set(Path('cases/thermal-recovery').glob('*'))|
             {Path(p) for p in ('tests/test_thermal_recovery.py','tests/test_recovery_curriculum.py','tests/test_generator_thermal.py','benchmarks/rms_equilibrium_installed_replay.py','plans/CURRENT_STATUS.md','plans/agent-designer.yaml','README.md','README.en.md')})
    for p in sources:
        dest=root/'sources'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    result=dict(passed=True,code_sha256=fp,wheel_sha256=digest(wheel.read_bytes()),previous_wheel_sha256=digest(old.read_bytes()),
        changed_runtime_files=changed,added_runtime_files=added,evidence_manifests=anchors,test_inventories=inventories,
        targeted_tests=520,new_tests=54,installed_simulation_checks=96,scripted_train_episodes=18,training_families=1,
        training_completed=False,images=False,independent_host=False,authenticated=False,actions=expected,
        independent_trajectories=40,comparisons=160,reference_clock_segments=1024,scope=q['scope'])
    (root/'delivery.json').write_bytes(encoded(result))
    if fp!=package_fingerprint() or anchors!={p:verify(p) for p in ROOTS}:raise ValueError('evidence changed')
    seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);print(deliver(**vars(p.parse_args())))
