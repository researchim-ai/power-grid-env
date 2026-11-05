"""CSC primary solver versus independent native OEL2C network feedback."""
import argparse,json
from pathlib import Path
from benchmarks.jacobian_scaling_validation import case as original
from benchmarks.oel2c_jacobian_native_validation import qualify as native

NAMES=('oel_regulating','batch_four')


def case(name):
    s=original(name);s.coupled.jacobian_format='csc';return s


def qualify(out,root):
    result=native(out,root,names=NAMES,case_factory=case)
    for name in NAMES:
        report=json.loads(Path(out,name,'primary.json').read_text())
        if report['integration']['jacobian_format']!='csc' or report['integration']['jacobian_linear_solver']!='SuperLU':raise ValueError('native comparison did not use CSC primary')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args();qualify(a.out,a.root)
