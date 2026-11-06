"""Private one-shot engineering worker; arguments are data paths, never code."""
import sys
from pathlib import Path
from power_grid_env.study_artifacts import read_json,encoded,digest
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json


def main():
    spec_path,out,options_path=map(Path,sys.argv[1:4]);payload=read_json(spec_path);options=read_json(options_path)
    if set(options)!={'resume','max_steps','checkpoint_every','cancel_file'}:raise ValueError('unknown worker options')
    spec=StudySpec.model_validate(payload);code=package_fingerprint();identity=digest(encoded(spec.model_dump(mode='json')))
    if spec.study=='qsts':
        cancel=Path(options['cancel_file'])
        report=run_study(spec,out=out/'engine',resume=options['resume'],max_steps=options['max_steps'],checkpoint_every=options['checkpoint_every'],cancelled=cancel.exists)
    else:
        if options['resume'] or options['max_steps'] is not None:raise ValueError('pause/resume is QSTS-only')
        report=run_study(spec)
    if code!=package_fingerprint() or digest(encoded(read_json(spec_path)))!=identity:raise ValueError('worker producer or input changed')
    report['workbench_execution']=dict(code_sha256=code,study_sha256=identity,isolated_process=True)
    write_json(out/'result.json',report)
    return 0

if __name__=='__main__':raise SystemExit(main())
