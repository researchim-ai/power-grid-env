"""Native network comparison with independent finite boundary audits."""
from pathlib import Path
import argparse,json
from benchmarks.generator_oel2c_boundary_cases import NAMES,NEGATIVE,example,negative
from benchmarks.generator_oel2c_selected_validation import run as qualify


def run(out,root,names=NAMES):
    return qualify(out,root,names,case_factory=example,negative_factory=negative,registered_names=NAMES,negative_names=NEGATIVE,require_boundary_audit=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));a=p.parse_args();print(json.dumps(run(a.out,a.root,tuple(a.cases)),indent=2))
