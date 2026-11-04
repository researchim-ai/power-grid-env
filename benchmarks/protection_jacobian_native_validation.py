"""Native ESST1A with stock MNLEX2 and authored thermal/PI block graphs."""
import argparse
from pathlib import Path
from benchmarks.esst1a_jacobian_native_validation import qualify as native
from benchmarks.protection_jacobian_validation import case

NAMES=('esst_field_trip','esst_uel_summation','ex02_overload_recovery','ex02_field_trip','ex02_generator_open','ex02_pss_limited')


def qualify(out,root):return native(out,root,names=NAMES,case_factory=case)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args();qualify(a.out,a.root)
