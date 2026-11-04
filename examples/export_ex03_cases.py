"""Export reproducible large StudySpecs without checking generated networks into git."""
from pathlib import Path
import argparse,json,os,sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from benchmarks.ex03_validation import NAMES,case
from power_grid_env.model import canonical_hash
from power_grid_env.report import write_json


def export(out,names):
    if not names or len(set(names))!=len(names) or not set(names)<=set((*NAMES,'native_avr')):raise ValueError('unique registered EX03 cases required')
    out=Path(out).resolve()
    if out.exists() and any(out.iterdir()):raise ValueError('fresh export directory required')
    manifest=json.loads((ROOT/'requirements/ex03-case-manifest.json').read_text())['cases'];cwd=Path.cwd()
    try:
        os.chdir(ROOT)
        studies={n:case(n) for n in names}
    finally:os.chdir(cwd)
    for name,s in studies.items():
        if canonical_hash(s)!=manifest[name]['sha256']:raise ValueError('EX03 case changed since qualification: '+name)
    out.mkdir(parents=True,exist_ok=True)
    for name,s in studies.items():write_json(out/('ex03_'+name+'.json'),s.model_dump(mode='json'))
    write_json(out/'manifest.json',{name:manifest[name] for name in names})
    return out

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--cases',nargs='+',choices=(*NAMES,'native_avr'),default=(*NAMES,'native_avr'));a=p.parse_args();print(export(a.out,a.cases))
