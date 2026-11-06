"""Bounded data-only study artifacts. Integrity is not source authentication."""
from pathlib import Path,PurePosixPath
import hashlib,json,math,stat,zipfile,os,tempfile

MAX_FILE=64_000_000
MAX_TOTAL=256_000_000
MAX_FILES=4096


def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(raw):return hashlib.sha256(raw).hexdigest()

def read_json(path):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>MAX_FILE:raise ValueError('unsafe or oversized JSON artifact')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate JSON key')
            result[key]=value
        return result
    def number(value):
        x=float(value)
        if not math.isfinite(x):raise ValueError('nonfinite JSON number')
        return x
    def reject(value):raise ValueError('nonfinite JSON constant')
    return json.loads(path.read_bytes(),object_pairs_hook=pairs,parse_float=number,parse_constant=reject)


def safe_name(value):
    p=PurePosixPath(value)
    if not value or p.is_absolute() or '\\' in value or ':' in value or '\x00' in value or any(x in {'','..','.'} for x in value.split('/')):raise ValueError('unsafe artifact path')
    return value


def inventory(root):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():raise ValueError('artifact root must be a real directory')
    files={};total=0
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ValueError('artifact symlink forbidden')
        if not path.is_file():continue
        name=safe_name(path.relative_to(root).as_posix())
        if name=='manifest.json':continue
        size=path.stat().st_size;total+=size
        if size>MAX_FILE or total>MAX_TOTAL or len(files)>=MAX_FILES:raise ValueError('artifact file/byte budget exceeded')
        files[name]=dict(bytes=size,sha256=digest(path.read_bytes()))
    return files


def seal(root):
    root=Path(root);manifest=dict(schema='power-grid-env.study-artifacts/v1',files=inventory(root),authenticated=False)
    path=root/'manifest.json'
    if path.exists():raise ValueError('sealed artifact cannot be overwritten')
    # Publish only complete bytes. A process crash before link() leaves no
    # apparently sealed artifact; link() also refuses concurrent overwriting.
    fd,name=tempfile.mkstemp(prefix='.pge-manifest-',dir=root.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(encoded(manifest));stream.flush();os.fsync(stream.fileno())
        try:os.link(temporary,path)
        except FileExistsError:raise ValueError('sealed artifact cannot be overwritten') from None
    finally:temporary.unlink(missing_ok=True)
    return digest(path.read_bytes())


def verify(root):
    root=Path(root);manifest=read_json(root/'manifest.json')
    if not isinstance(manifest,dict) or manifest.get('schema')!='power-grid-env.study-artifacts/v1' or manifest.get('files')!=inventory(root):raise ValueError('artifact inventory or content changed')
    return digest((root/'manifest.json').read_bytes())


def export_zip(root,target):
    root=Path(root).resolve();target=Path(target).absolute();before=verify(root)
    if target.is_relative_to(root) or target.exists():raise ValueError('new export outside artifact required')
    target.parent.mkdir(parents=True,exist_ok=True)
    archive=zipfile.ZipFile(target,'x',compression=zipfile.ZIP_STORED)
    try:
        with archive:
            for p in sorted(root.rglob('*')):
                if p.is_file():
                    item=zipfile.ZipInfo(p.relative_to(root).as_posix(),date_time=(1980,1,1,0,0,0));item.external_attr=(stat.S_IFREG|0o644)<<16;archive.writestr(item,p.read_bytes())
        if verify(root)!=before:raise ValueError('artifact changed while exporting')
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return dict(sha256=digest(target.read_bytes()),manifest_sha256=before)


def extract_zip(source,target):
    target=Path(target)
    if target.exists():raise ValueError('new extraction directory required')
    if Path(source).stat().st_size>MAX_TOTAL+4_000_000:raise ValueError('archive byte budget exceeded')
    with zipfile.ZipFile(source) as archive:
        entries=archive.infolist();seen=set();total=0
        if not entries or len(entries)>MAX_FILES:raise ValueError('archive file budget exceeded')
        for item in entries:
            name=safe_name(item.filename);folded=name.casefold();total+=item.file_size
            mode=item.external_attr>>16
            if folded in seen or item.is_dir() or item.flag_bits&1 or stat.S_IFMT(mode) not in (0,stat.S_IFREG) or item.file_size>MAX_FILE or total>MAX_TOTAL:raise ValueError('unsafe archive member')
            seen.add(folded)
        target.mkdir(parents=True)
        try:
            for item in entries:
                path=target/item.filename;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(archive.read(item))
            verify(target)
        except BaseException:
            import shutil
            shutil.rmtree(target);raise
    return target
