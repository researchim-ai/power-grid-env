"""Prepare the hash-pinned OpenIPSL/MSL4 and FMI master; no Dynawo dependency."""
from pathlib import Path
import argparse,json,urllib.request,zipfile
from benchmarks.esst1a_native import LOCK,sha,verify
from benchmarks.modelica_reference_setup import prepare as prepare_base


def prepare(root,fetch=False):
    root=Path(root).resolve();prepare_base(root,fetch);item=json.loads(LOCK.read_text())['fmpy'];archive=root/'packages'/item['filename']
    if not archive.exists():
        if not fetch:raise ValueError('missing optional FMI master: '+str(archive))
        with urllib.request.urlopen(item['url'],timeout=60) as response:archive.write_bytes(response.read())
    if sha(archive)!=item['sha256']:raise ValueError('changed FMI master archive')
    with zipfile.ZipFile(archive) as z:
        if any(Path(n).is_absolute() or '..' in Path(n).parts for n in z.namelist()):raise ValueError('unsafe FMI master archive')
        z.extractall(root/'fmpy')
    return verify(root)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True,type=Path);parser.add_argument('--fetch',action='store_true');args=parser.parse_args();print(json.dumps(prepare(args.root,args.fetch),indent=2))
