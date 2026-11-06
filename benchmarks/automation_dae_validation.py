"""Independent SI automation references versus analytic Radau and network DAE."""
from pathlib import Path
import argparse,json
from benchmarks.network_automation_reference import example,reference,compare,NAMES,LIMITS
from power_grid_env.study_spec import run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json,sha256_file


def run(out):
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise ValueError('fresh automation DAE output required')
    code=package_fingerprint();producer=sha256_file(Path(__file__));checks=[]
    write_json(out/'protocol.json',dict(cases={n:example(n).model_dump(mode='json') for n in NAMES},modes=['radau','network_dae'],repeats=2,limits=LIMITS,code_sha256=code,producer_sha256=producer))
    for name in NAMES:
        original=example(name);native=reference(name,original);write_json(out/name/'reference.json',native)
        for mode in ['radau','network_dae']:
            previous=None
            for repeat in range(2):
                s=original.model_copy(deep=True);s.coupled.integration_method=mode;s.coupled.jacobian_network='direct_solve';s.coupled.jacobian_format='csc'
                primary=run_study(s,isolate=True,wall_timeout_s=120);write_json(out/name/mode/str(repeat)/'primary.json',primary)
                check=compare(primary,native)
                check['actual_analytic_path']=primary['integration']['jacobian_assembly']==('analytic_Schur_products' if mode=='network_dae' else 'column_blocks')
                check['passed']=check['passed'] and check['actual_analytic_path']
                if previous is not None:
                    check['repeat_trace_equality']=all(primary[k]==previous[k] for k in ['time_s','sample_side','channels','event_log']);check['passed'] &= check['repeat_trace_equality']
                previous=primary;write_json(out/name/mode/str(repeat)/'comparison.json',check);checks.append(check);print(name,mode,repeat,check['passed'],flush=True)
    if code!=package_fingerprint() or producer!=sha256_file(Path(__file__)):raise ValueError('automation DAE producer changed during validation')
    result=dict(passed=all(c['passed'] for c in checks),cases=len(NAMES),comparisons=len(checks),code_sha256=code,producer_sha256=producer,independent_host=False,
        analytic_continuous_modes=True,global_hybrid_gradient=False,arbitrary_unbalanced_dynamics=False,
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json')) if p.name!='summary.json'})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);a=p.parse_args();r=run(a.out);print(json.dumps(r,indent=2));raise SystemExit(0 if r['passed'] else 1)
