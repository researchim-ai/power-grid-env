"""Verify the pinned second feeder, optionally acquiring only its declared OEDI files."""
from pathlib import Path
import argparse,hashlib,json,urllib.request,urllib.parse
from power_grid_env.study_artifacts import safe_name


def run(root=None,*,download=False):
    repo=Path(__file__).resolve().parents[1]
    root=repo/'data/external/raw' if root is None else Path(root)
    lock=json.loads((repo/'requirements/smartds-second.lock.json').read_text());verified=[];total=0
    for row in lock['files']:
        relative=safe_name(row['path']);path=root/relative;url=urllib.parse.urlparse(row['url'])
        if url.scheme!='https' or url.netloc!='oedi-data-lake.s3.amazonaws.com' or not url.path.startswith('/SMART-DS/v1.0/2018/SFO/P1U/'):
            raise ValueError('acquisition URL outside pinned official dataset')
        if row['bytes']>2_000_000 or total+row['bytes']>64_000_000:raise ValueError('acquisition byte budget exceeded')
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent!=root.parent):raise ValueError('symlink in raw acquisition path')
        if path.exists():raw=path.read_bytes()
        elif download:
            with urllib.request.urlopen(row['url'],timeout=30) as response:raw=response.read(row['bytes']+1)
        else:raise ValueError('missing pinned input; use explicit --download: '+relative)
        if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('pinned raw input differs: '+relative)
        if not path.exists():
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as stream:stream.write(raw)
        total+=len(raw);verified.append(relative)
    return dict(passed=True,files=len(verified),bytes=total,network_requested=download,source='official OEDI S3 only',policy='no_GitHub_no_CI')


def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--root',type=Path);p.add_argument('--download',action='store_true');a=p.parse_args();print(json.dumps(run(a.root,download=a.download)))

if __name__=='__main__':main()
