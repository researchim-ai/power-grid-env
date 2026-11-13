"""Pinned R06.16 adaptive-driver ablation against a sealed current run.

Only the adaptive driver is replaced in a local worker. The current electrical
solver, tolerances, parameters and PWM/control scheduler remain identical.
The frozen Python source must match its registered SHA256 before execution.
"""
import argparse
import hashlib
import sys
import types
from pathlib import Path
from benchmarks.extended_closed_loop_acceptance import document
import power_grid_env.switching_adaptive as adaptive
from power_grid_env.switching_drive import run_switching_drive,SwitchingDriveStudy
from power_grid_env.switching_convergence import differences,audited,SwitchingTemporalLimits
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,verify,seal

FROZEN_SHA256='b5ce84034aa6805af608488de9e6b661da105feb745f0ff1b3bd57de331b6fbd'


def frozen_driver(path):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=FROZEN_SHA256:raise ValueError('frozen adaptive driver hash differs')
    name='_power_grid_frozen_r0616_adaptive';module=types.ModuleType(name);sys.modules[name]=module
    try:exec(compile(raw,str(path),'exec'),vars(module))
    finally:sys.modules.pop(name,None)
    return module


def run(frozen,current_root,historical_result,reference,out):
    driver=frozen_driver(frozen);root=Path(current_root);identity=verify(root);before=package_fingerprint()
    protocol=read_json(root/'protocol.json')
    if protocol['code_sha256']!=before:raise ValueError('current ablation producer differs')
    reference_raw=Path(reference).read_bytes();reference_sha256=hashlib.sha256(reference_raw).hexdigest()
    if protocol['references']['double_cage']!=reference_sha256:raise ValueError('ablation reference differs')
    reference_result=read_json(reference)
    level=root/'double_cage/level-1';doc=document('double_cage');doc['adaptive']={'error_budget_fraction':.1}
    p=SwitchingDriveStudy.model_validate(doc)
    if p.model_dump(mode='json')!=read_json(level/'study.json'):raise ValueError('ablation study differs')
    current=read_json(level/'result.json');historical=read_json(historical_result) if historical_result else None;original=adaptive.adaptive_step
    try:
        adaptive.adaptive_step=driver.adaptive_step
        baseline=run_switching_drive(doc)
    finally:adaptive.adaptive_step=original
    comparable=dict(baseline,integration=dict(baseline['integration']));comparable['integration'].pop('motor_boundaries',None)
    reproduced=comparable==historical if historical is not None else None;q=SwitchingTemporalLimits()
    old=differences(p,baseline,reference_result,q)
    new=differences(p,current,reference_result,q)
    audits={name:audited(p,r,q) for name,r in [('frozen',baseline),('current',current)]}
    if package_fingerprint()!=before:raise RuntimeError('ablation producer changed during run')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);(out/'frozen-result.json').write_bytes(encoded(baseline))
    summary=dict(schema='power-grid-env.motor-zero-ablation/v1',passed=reproduced is not False and not old['passed'] and new['passed'] and all(all(a.values()) for a in audits.values()) and current['integration']['motor_boundaries']>0,baseline_reproduced_exactly=reproduced,baseline_difference=old,current_difference=new,audits=audits,code_sha256=before,frozen_driver_sha256=FROZEN_SHA256,current_run_manifest=identity,reference_sha256=reference_sha256,historical_result_sha256=hashlib.sha256(Path(historical_result).read_bytes()).hexdigest() if historical_result else None,independent_reference=False,absolute_error_certified=False,scope='pinned adaptive-driver intervention; identical electrical solver and declared limits; current run is reused from sealed matching-source evidence')
    (out/'study.json').write_bytes(encoded(p.model_dump(mode='json')));(out/'summary.json').write_bytes(encoded(summary));seal(out);return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--frozen',default=str(Path(__file__).with_name('switching_adaptive_r0616.py')));parser.add_argument('--current-root',required=True);parser.add_argument('--historical-result',help='optional archived exact replay check');parser.add_argument('--reference',required=True);parser.add_argument('--out',required=True)
    a=parser.parse_args();r=run(a.frozen,a.current_root,a.historical_result,a.reference,a.out);print(r['passed'],flush=True);raise SystemExit(0 if r['passed'] else 2)
