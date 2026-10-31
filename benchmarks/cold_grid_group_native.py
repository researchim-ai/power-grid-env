"""Preregistered cold DOL group from the grid, with sag and automatic restart."""
from pathlib import Path
import argparse
from power_grid_env.study_spec import StudySpec
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint
from benchmarks.automatic_group_native import compare,STEPS


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    case=Path('cases/studies/automatic_group_grid_cold.json')
    write_json(a.out/'summary.json',dict(passed=False,status='running',case_file_sha256=sha256_file(case)))
    try:
        s=StudySpec.model_validate_json(case.read_text());r=compare(s,steps=STEPS,out=a.out/'grid_cold')
        write_json(a.out/'summary.json',dict(passed=r['passed'],cases=[r],code_sha256=package_fingerprint(),case_file=str(case),case_file_sha256=sha256_file(case),source_sha256=sha256_file(Path(__file__)),native_steps_s=list(STEPS),field_validated=False,industrial_profile_qualified=False))
    except Exception as e:
        write_json(a.out/'summary.json',dict(passed=False,status='failed',error=str(e)));raise
    if not r['passed']:raise SystemExit(1)


if __name__=='__main__':main()
