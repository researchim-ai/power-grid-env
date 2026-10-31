"""Offline wheel/venv qualification candidate for the motor-start pilot.

Requires a prepared runtime wheelhouse and the committed platform hash lock.
No editable install, system-site packages, source PYTHONPATH or network resolver.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile
import email
from importlib.metadata import version
from power_grid_env.evidence import package_fingerprint


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def qualify(work,out,wheelhouse):
    if platform.system()!='Linux' or platform.machine()!='x86_64' or sys.version_info[:2]!=(3,12):raise ValueError('qualified candidate is Linux x86_64 CPython 3.12 only')
    work=Path(work).resolve();out=Path(out).resolve();wheelhouse=Path(wheelhouse).resolve();repo=Path(__file__).resolve().parents[1]
    if work==out or work.is_relative_to(out) or out.is_relative_to(work):raise ValueError('work and report directories must be disjoint')
    for root in [work,out]:
        if root.exists() and any(root.iterdir()):raise ValueError('work/report directory must be new or empty: '+str(root))
        root.mkdir(parents=True,exist_ok=True)
    def build_inputs():
        files=[p for p in (repo/'power_grid_env').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
        files.extend(repo/name for name in ['pyproject.toml','README.md','LICENSE'] if (repo/name).exists())
        return {str(p.relative_to(repo)):sha(p) for p in sorted(files)}
    inputs_before=build_inputs()
    (out/'build-inputs.json').write_text(json.dumps(inputs_before,indent=2)+'\n')
    identity=package_fingerprint();env=os.environ.copy();env.pop('PYTHONPATH',None);env['SOURCE_DATE_EPOCH']='1793404800';env['PYTHONNOUSERSITE']='1'
    logs={};counter=0
    def execute(label,args,cwd,timeout=180):
        nonlocal counter
        counter+=1
        result=subprocess.run([str(a) for a in args],cwd=cwd,env=env,capture_output=True,text=True,timeout=timeout)
        log=out/f'{counter:02d}-{label}.log';log.write_text(result.stdout+'\nSTDERR:\n'+result.stderr);logs[log.name]=sha(log)
        if result.returncode:raise RuntimeError(label+' failed: '+result.stderr[-3000:])
        return result.stdout
    wheels=[]
    for number in [1,2]:
        directory=work/f'build-{number}';directory.mkdir()
        execute('wheel-build',[sys.executable,'-m','pip','--isolated','wheel','--no-deps','--no-build-isolation','-w',directory,repo],work)
        found=list(directory.glob('power_grid_env-*.whl'));assert len(found)==1;wheels.append(found[0])
    if sha(wheels[0])!=sha(wheels[1]):raise ValueError('wheel builds differ at fixed SOURCE_DATE_EPOCH')
    packaged=wheelhouse/wheels[0].name;shutil.copyfile(wheels[0],packaged)
    runtime_lock=repo/'requirements/pilot-linux-py312.lock'
    install_lock=out/'install-requirements.lock';
    with zipfile.ZipFile(packaged) as z:
        metadata=email.message_from_bytes(z.read(next(n for n in z.namelist() if n.endswith('.dist-info/METADATA'))))
    install_lock.write_text(runtime_lock.read_text()+f"{metadata['Name']}=={metadata['Version']} --hash=sha256:{sha(packaged)}\n")
    inputs=out/'inputs';inputs.mkdir()
    for source,name in [('cases/studies/thermal_cold_trip.json','study.json'),('cases/replay/motor-start.json','criteria.json'),
                        ('cases/measurements/synthetic_motor_cold_trip.csv','trace.csv'),('cases/measurements/synthetic_motor_cold_trip.protocol.json','trace-protocol.json')]:
        shutil.copyfile(repo/source,inputs/name)
    executables=[];probes=[]
    for label in ['a','b']:
        venv=work/('venv-'+label);execute('create-venv-'+label,[sys.executable,'-m','venv',venv],work)
        python=venv/'bin/python';executables.append(python)
        if 'include-system-site-packages = false' not in (venv/'pyvenv.cfg').read_text().lower():raise ValueError('venv is not isolated')
        execute('offline-install-'+label,[python,'-I','-m','pip','--isolated','install','--no-index','--find-links',wheelhouse,'--require-hashes','-r',install_lock],work)
        execute('pip-check-'+label,[python,'-I','-m','pip','check'],work)
        cwd=work/('empty-'+label);cwd.mkdir()
        probe="""
import json,sys
from pathlib import Path
import power_grid_env
from power_grid_env.motor_bundle import runtime_identity
from power_grid_env.evidence import package_fingerprint
from power_grid_env.build import NetBuilder
from power_grid_env.backend_opendss import solve_network
assert sys.prefix!=sys.base_prefix
assert Path(power_grid_env.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
assert sys.argv[1] not in sys.path
b=NetBuilder('clean-pf','Clean PF',10.,s_base_va=1e6)
b.node('src');b.node('load');b.source('src',.05,.2)
b.line_z1('src','load',.2+.4j,line_id='line');b.load('load',100000.,20000.,load_id='load')
r=solve_network(b.build());assert r.status=='converged'
print(json.dumps(dict(isolated_venv=True,import_path=power_grid_env.__file__,source_root_in_sys_path=False,
    code_sha256=package_fingerprint(),runtime=runtime_identity(),opendss_status=r.status)))
"""
        output=execute('import-probe-'+label,[python,'-I','-c',probe,repo],cwd)
        parsed=json.loads(output.strip().splitlines()[-1]);probes.append(parsed)
        if parsed['code_sha256']!=identity:raise ValueError('installed code fingerprint differs from source')
        (out/f'import-probe-{label}.json').write_text(json.dumps(parsed,indent=2)+'\n')
    python_a,python_b=executables
    execute('seal-bundle',[python_a,'-I','-m','power_grid_env.cli','motor-bundle','create','--study',inputs/'study.json','--criteria',inputs/'criteria.json',
        '--trace',inputs/'trace.csv','--trace-protocol',inputs/'trace-protocol.json','--out',out/'bundle'],work/'empty-a')
    for label,python in zip(['a','b'],executables):
        execute('replay-'+label,[python,'-I','-m','power_grid_env.cli','motor-bundle','replay',out/'bundle','--out',out/f'replay-{label}.json'],work/('empty-'+label))
        replay=json.loads((out/f'replay-{label}.json').read_text())
        if not(replay['completed'] and replay['repeatable'] and replay['measurement_comparison_passed']):raise ValueError('installed replay or synthetic SI comparison failed')
    if package_fingerprint()!=identity or build_inputs()!=inputs_before:raise ValueError('build inputs changed during qualification')
    result=dict(status='passed_candidate_checks',code_sha256=identity,platform=dict(system=platform.system(),machine=platform.machine(),python=platform.python_version()),
        wheel_sha256=sha(packaged),wheel_filename=packaged.name,repeat_build_same_sha256=True,runtime_lock_sha256=sha(runtime_lock),install_lock_sha256=sha(install_lock),
        clean_venvs=2,system_site_packages=False,editable_install=False,offline_hash_checked_install=True,pip_check_passed=True,
        repository_independent_import=True,motor_replay_passed=True,opendss_smoke_passed=True,synthetic_trace_comparison_passed=True,
        build_tools={name:version(name) for name in ['pip','setuptools','wheel']},runtime=probes[0]['runtime'],
        other_machine_validation=False,field_validated=False,industrial_profile_qualified=False,
        scope_note='Two isolated virtual environments on the same host; synthetic motor trace only; not independent field acceptance')
    shutil.copyfile(packaged,out/packaged.name)
    (out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    files={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}
    (out/'manifest.json').write_text(json.dumps(files,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--wheelhouse',type=Path,required=True)
    args=p.parse_args();result=qualify(args.work,args.out,args.wheelhouse);print(json.dumps(result,indent=2))


if __name__=='__main__':main()
