"""Complete physical-study finite differences for the RMS variational observer."""
from pathlib import Path
import argparse
from benchmarks.rms_scalability import example
from power_grid_env.induction import InductionEvent
from power_grid_env.motor_thermal import MotorThermalProtection
from power_grid_env.hybrid_sensitivity import audit_initial_sensitivity
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint


def case(thermal=False):
    s=example(4);p=s.induction;p.duration_s=.35;p.step_s=.005;p.rtol=1e-10;p.atol=1e-12
    p.event_policy='coincident_roots_v1';p.motors[0].connected=False
    p.events=[InductionEvent(time_s=t,kind=k,equipment_id='m') for t,k in ((.1,'motor_close'),(.1,'motor_open'),(.2,'motor_close'))]
    if thermal:p.motors[0].thermal=MotorThermalProtection(time_constant_s=.1,trip_level=.5)
    return s


def run(out):
    out=Path(out)
    if out.exists():raise ValueError('fresh trajectory validation directory required')
    code=package_fingerprint();rows=[]
    producers={p.name:sha256_file(p) for p in sorted(Path(__file__).parent.glob('*.py'))}
    write_json(out/'protocol.json',dict(code_sha256=code,producer_sources_sha256=producers,producer_sha256=sha256_file(Path(__file__)),
        studies={str(t):case(t).model_dump(mode='json') for t in (False,True)},
        steps=[4e-5,2e-5,1e-5],reference='unobserved primary physical trajectories; no tangent RHS or saltation',
        parameter_scope='initial physical-state directions, not equipment parameter gradients'))
    for thermal in (False,True):
        s=case(thermal);r=audit_initial_sensitivity(s.network,s.induction,[.25,.5,.75,1.] if thermal else [1/3,2/3,1.])
        write_json(out/str(thermal)/'comparison.json',r);rows.append(r)
    if code!=package_fingerprint() or producers!={p.name:sha256_file(p) for p in sorted(Path(__file__).parent.glob('*.py'))}:raise ValueError('trajectory producer changed')
    result=dict(passed=all(r['passed'] for r in rows),code_sha256=code,cases=rows,
        controller_reset_derivatives=False,ambiguous_simultaneous_guards_rejected=True,
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json'))})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    raise SystemExit(0 if run(a.out)['passed'] else 1)
