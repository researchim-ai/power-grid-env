"""Optional hash-pinned, rootless Modelica reference runtime; never a core dependency."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess,tarfile,urllib.request,zipfile

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/'requirements/modelica-reference.json'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root,fetch=False):
    root=Path(root).resolve();lock=json.loads(LOCK.read_text());packages=root/'packages';packages.mkdir(parents=True,exist_ok=True)
    for item in lock['packages'].values():
        p=packages/item['filename']
        if not p.exists():
            if not fetch:raise ValueError('missing optional reference archive: '+str(p))
            with urllib.request.urlopen(item['url'],timeout=60) as response:p.write_bytes(response.read())
        if digest(p)!=item['sha256']:raise ValueError('reference archive hash mismatch: '+p.name)
        if p.suffix=='.deb':subprocess.run(['dpkg-deb','-x',str(p),str(root/'runtime')],check=True)
        else:
            with zipfile.ZipFile(p) as z:
                if any(Path(n).is_absolute() or '..' in Path(n).parts for n in z.namelist()):raise ValueError('unsafe archive path')
                z.extractall(root/'libraries')
    return verify(root)


def verify(root):
    root=Path(root).resolve();lock=json.loads(LOCK.read_text());checked=0
    for item in lock['packages'].values():
        p=root/'packages'/item['filename']
        if not p.is_file() or digest(p)!=item['sha256']:raise ValueError('missing or changed reference archive: '+str(p))
        if p.suffix=='.deb':
            proc=subprocess.Popen(['dpkg-deb','--fsys-tarfile',str(p)],stdout=subprocess.PIPE)
            try:
                with tarfile.open(fileobj=proc.stdout,mode='r|') as tar:
                    for member in tar:
                        target=root/'runtime'/member.name
                        if member.isfile():
                            data=tar.extractfile(member).read()
                            if not target.is_file() or hashlib.sha256(data).hexdigest()!=digest(target):raise ValueError('changed Modelica runtime file: '+member.name)
                            checked+=1
                        elif member.issym() and (not target.is_symlink() or os.readlink(target)!=member.linkname):raise ValueError('changed runtime symlink')
            finally:
                proc.stdout.close();code=proc.wait()
            if code:raise ValueError('reference package inspection failed')
        else:
            with zipfile.ZipFile(p) as z:
                for name in z.namelist():
                    if not name.endswith(('.mo','package.order')) and '/LICENSE' not in name:continue
                    if name.endswith('/'):continue
                    target=root/'libraries'/name
                    if not target.is_file() or digest(target)!=hashlib.sha256(z.read(name)).hexdigest():raise ValueError('changed Modelica source: '+name)
                    checked+=1
    env=environment(root)
    version=subprocess.check_output([str(root/'runtime/usr/bin/omc'),'--version'],env=env,text=True,timeout=15).strip()
    if version!='OpenModelica '+lock['openmodelica_version']:raise ValueError('unexpected native compiler version')
    return dict(openmodelica=version,openipsl_commit=lock['openipsl_commit'],modelica_version=lock['modelica_version'],verified_runtime_and_source_files=checked,lock_sha256=digest(LOCK))


def environment(root):
    prefix=Path(root).resolve()/'runtime/usr';env=dict(os.environ)
    env['OPENMODELICAHOME']=str(prefix)
    env['LD_LIBRARY_PATH']=':'.join([str(prefix/'lib/x86_64-linux-gnu'),str(prefix/'lib/x86_64-linux-gnu/omc'),env.get('LD_LIBRARY_PATH','')])
    env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    return env


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);p.add_argument('--fetch',action='store_true');a=p.parse_args()
    print(json.dumps(prepare(a.root,a.fetch),indent=2))
