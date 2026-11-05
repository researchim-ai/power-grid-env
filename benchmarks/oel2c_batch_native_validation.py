"""Independent native feedback for explicitly opted-in multiple-OEL profiles."""
import argparse
from pathlib import Path
from benchmarks.oel2c_jacobian_native_validation import qualify as native
from benchmarks.oel2c_batch_validation import case

NAMES=('cold__two_limiters','cold__four_machines','loaded__two_limiters','active__mixed_modes','regulating__two_regulators','selected__two_selected')


def qualify(out,root):return native(out,root,names=NAMES,case_factory=case)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args();qualify(a.out,a.root)
