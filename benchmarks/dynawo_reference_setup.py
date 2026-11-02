"""Optional exact Dynawo dependency closure, MSL 3.2.3 and FMI master tooling."""
from pathlib import Path
import argparse,json,hashlib,urllib.request,zipfile,shutil
from benchmarks.modelica_reference_setup import verify as verify_base,environment

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/'requirements/dynawo-oel2c-reference.json'
PREFIX='dynawo/sources/Models/Modelica/'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def prepare(root,fetch=False):
    root=Path(root).resolve();verify_base(root);lock=json.loads(LOCK.read_text());packages=root/'packages'
    for item in [lock['MSL'],lock['fmpy']]:
        p=packages/item['filename']
        if not p.exists():
            if not fetch:raise ValueError('missing optional archive: '+str(p))
            p.write_bytes(urllib.request.urlopen(item['url'],timeout=60).read())
        if sha(p)!=item['sha256']:raise ValueError('changed Dynawo reference archive')
        target=root/('libraries' if p.suffix=='.zip' else 'fmpy')
        with zipfile.ZipFile(p) as z:
            if any(Path(n).is_absolute() or '..' in Path(n).parts for n in z.namelist()):raise ValueError('unsafe reference archive')
            z.extractall(target)
    for name,item in lock['files'].items():
        target=root/'dynawo-source'/name
        if not target.exists():
            if not fetch:raise ValueError('missing Dynawo source: '+str(target))
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(urllib.request.urlopen(item['url'],timeout=30).read())
        if sha(target)!=item['sha256']:raise ValueError('changed pinned Dynawo source')
    install_guarded_copy(root)
    return verify(root)


def verify(root):
    root=Path(root).resolve();base=verify_base(root);lock=json.loads(LOCK.read_text());count=0
    for name,item in lock['files'].items():
        p=root/'dynawo-source'/name
        if not p.is_file() or sha(p)!=item['sha256']:raise ValueError('missing or changed Dynawo source: '+name)
        count+=1
    verify_guarded_copy(root)
    actual={str(p.relative_to(root/'dynawo-source')) for p in (root/'dynawo-source').rglob('*.mo')}
    if actual!={k for k in lock['files'] if k.endswith('.mo')}:raise ValueError('unregistered Dynawo Modelica source')
    for label,folder in [('MSL','libraries'),('fmpy','fmpy')]:
        item=lock[label];p=root/'packages'/item['filename']
        if not p.is_file() or sha(p)!=item['sha256']:raise ValueError('changed reference archive: '+label)
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if n.endswith('/') or '/__pycache__/' in n:continue
                target=root/folder/n
                if not target.is_file() or sha(target)!=hashlib.sha256(z.read(n)).hexdigest():raise ValueError('changed installed reference file: '+n)
                count+=1
    return dict(base_runtime=base,dynawo_commit=lock['commit'],modelica_version='3.2.3',fmpy_version=lock['fmpy']['version'],verified_additional_files=count,reference_variant='cap-guard-v1',activation_guard_sha256=sha(root/'dynawo-cap-guard-v1'/PATCH_FILE),lock_sha256=sha(LOCK))


PATCH_FILE='dynawo/sources/Models/Modelica/Dynawo/Electrical/Controls/Machines/OverExcitationLimiters/Standard/BaseClasses/OelActivation.mo'


def guarded_bytes(name,original):
    if name!=PATCH_FILE:return original
    text=original.decode()
    old='IRefPu == IInstPu'
    if text.count(old)!=1 or text.count('  //Input variables')!=1:raise ValueError('unexpected cap-guard patch base')
    text=text.replace('  //Input variables', '  parameter Types.CurrentModulePu ICapTolPu=1e-8 "Numerical upper-cap recognition band, explicit guarded variant v1";\n\n  //Input variables')
    return text.replace(old,'IRefPu >= IInstPu - ICapTolPu').encode()


def install_guarded_copy(root):
    root=Path(root);lock=json.loads(LOCK.read_text())
    for name in lock['files']:
        source=root/'dynawo-source'/name;target=root/'dynawo-cap-guard-v1'/name
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(guarded_bytes(name,source.read_bytes()))


def verify_guarded_copy(root):
    lock=json.loads(LOCK.read_text());root=Path(root)
    for name in lock['files']:
        source=root/'dynawo-source'/name;target=root/'dynawo-cap-guard-v1'/name
        if not target.is_file() or target.read_bytes()!=guarded_bytes(name,source.read_bytes()):raise ValueError('changed numerical-guard reference variant: '+name)
    expected={name for name in lock['files'] if name.endswith('.mo')}
    actual={str(p.relative_to(root/'dynawo-cap-guard-v1')) for p in (root/'dynawo-cap-guard-v1').rglob('*.mo')}
    if actual!=expected:raise ValueError('unregistered guarded Modelica source')


def library_paths(root,variant='guarded'):

    root=Path(root).resolve();msl=root/'libraries/ModelicaStandardLibrary-3.2.3'
    return [msl/'Complex.mo',msl/'ModelicaServices/package.mo',msl/'Modelica/package.mo',root/('dynawo-cap-guard-v1' if variant=='guarded' else 'dynawo-source')/PREFIX/'Dynawo/package.mo']


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--fetch',action='store_true');a=p.parse_args();print(json.dumps(prepare(a.root,a.fetch),indent=2))
