"""Installed-wheel, local-only balanced AC workflow acceptance through the CLI."""
from pathlib import Path
import argparse
import cmath
import hashlib
import json
import os
import shutil
import subprocess


def qualify(python, source, out):
    source = Path(source).resolve(); out = Path(out).resolve()
    if out.exists(): raise ValueError('new acceptance directory required')
    out.mkdir(parents=True); records = []
    env = os.environ.copy(); env.pop('PYTHONPATH', None)
    env['PYTHONNOUSERSITE'] = '1'; env['OPENBLAS_NUM_THREADS'] = '1'
    def cli(*args, expected=0):
        command = [str(python), '-I', '-m', 'power_grid_env.cli', 'steady-study', *map(str, args)]
        run = subprocess.run(command, cwd=out, env=env, capture_output=True, text=True, timeout=180)
        i = len(records); (out/f'{i:02d}.stdout').write_text(run.stdout); (out/f'{i:02d}.stderr').write_text(run.stderr)
        records.append(dict(argv=command, expected=expected, returncode=run.returncode))
        if run.returncode != expected: raise ValueError('installed CLI gate failed: '+run.stdout[-2000:]+run.stderr[-1000:])
        return json.loads(run.stdout)
    def document(task, case, filename, **extra):
        path = out/filename
        path.write_text(json.dumps(dict(schema='power-grid-env.steady-study/v1', id='installed-'+task, task=task, case=case, **extra)))
        return path
    cli('import-matpower', '--source', source/'cases/reference/balanced_ac_three_bus.m', '--out', out/'case.json')
    case = json.loads((out/'case.json').read_text())
    optimized = cli('run', '--study', document('opf', case, 'opf-input.json'), '--out', out/'opf')
    cli('derive', out/'opf', '--task', 'power_flow', '--out', out/'derived-pf.json')
    pf = cli('run', '--study', out/'derived-pf.json', '--out', out/'pf')
    if not optimized['success'] or not pf['within_limits']: raise ValueError('OPF/PF feasibility gate failed')
    if max(abs(a-b) for a, b in zip(optimized['vm_pu'], pf['vm_pu'])) > 1e-7: raise ValueError('dispatch replay differs')
    cli('derive', out/'opf', '--task', 'n1', '--out', out/'derived-n1.json')
    part = cli('run', '--study', out/'derived-n1.json', '--out', out/'part', '--max-cases', 1, expected=2)
    resumed = cli('run', '--study', out/'derived-n1.json', '--out', out/'resumed', '--resume-from', out/'part')
    full = cli('run', '--study', out/'derived-n1.json', '--out', out/'full')
    if part['status'] != 'paused' or resumed['journal_sha256'] != full['journal_sha256']: raise ValueError('N-1 continuation differs')
    meters = []
    truth = {10: 1.04+0j, 20: cmath.rect(1., -.03), 30: cmath.rect(.98, -.06)}
    for bus, v in truth.items():
        for repeat in range(2):
            meters.append(dict(id=f'v-{bus}-{repeat}', kind='vm_pu', value=abs(v), sigma=.001, bus_id=bus))
            if bus != 10: meters.append(dict(id=f'a-{bus}-{repeat}', kind='va_rad', value=cmath.phase(v), sigma=.001, bus_id=bus))
    estimator = cli('run', '--study', document('state_estimation', case, 'estimation-input.json', measurements=meters), '--out', out/'estimate')
    if not estimator['accepted'] or max(abs(v-truth[k]) for k, v in zip(estimator['bus_ids'], [cmath.rect(a, b*cmath.pi/180) for a, b in zip(estimator['vm_pu'], estimator['va_deg'])])) > 1e-8:
        raise ValueError('installed estimator differs from exact phasors')
    cli('verify', out/'resumed'); cli('verify', out/'estimate')
    cli('export', out/'estimate', '--out', out/'estimate.zip')
    cli('export', out/'estimate', '--out', out/'estimate.zip', expected=2)
    cli('run', '--study', out/'derived-pf.json', '--out', out/'pf', expected=2)
    cli('derive', out/'pf', '--task', 'n1', '--out', out/'invalid-derived.json', expected=2)
    cancel = out/'cancel'; cancel.touch()
    stopped = cli('run', '--study', out/'derived-n1.json', '--out', out/'cancelled', '--cancel-file', cancel, expected=2)
    if stopped['status'] != 'cancelled': raise ValueError('cancellation gate failed')
    cli('run', '--study', out/'derived-n1.json', '--out', out/'after-cancel', '--resume-from', out/'cancelled')
    shutil.copytree(out/'estimate', out/'tampered'); (out/'tampered/extra').write_text('unexpected')
    cli('verify', out/'tampered', expected=2)
    result = dict(passed=True, code_sha256=optimized['code_sha256'], commands=records,
                  continuation_byte_identical=True, policy='local_only_no_GitHub_no_CI',
                  scope='installed authored three-bus PF/OPF/AC estimation/N-1 and negative artifact cases')
    (out/'acceptance.json').write_text(json.dumps(result, indent=2)+'\n')
    (out/'evidence-manifest.json').write_text(json.dumps({p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}, indent=2)+'\n')
    return result


def main():
    p = argparse.ArgumentParser(__doc__); p.add_argument('--python', type=Path, required=True); p.add_argument('--source', type=Path, default=Path('.')); p.add_argument('--out', type=Path, required=True); a = p.parse_args()
    result = qualify(a.python.absolute(), a.source, a.out)
    print(json.dumps(dict(passed=result['passed'], code_sha256=result['code_sha256'], commands=len(result['commands']))))

if __name__ == '__main__': main()
