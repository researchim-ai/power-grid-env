from pathlib import Path
import argparse
import numpy as np
from benchmarks.full_sensitivity_cases import NAMES,case,pv_oracle
from power_grid_env.study_sensitivity import differentiate_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint


def run(out):
    out=Path(out)
    if out.exists():raise ValueError('fresh sensitivity acceptance required')
    code=package_fingerprint();rows=[];producers={p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')}
    specs={name:case(name) for name in NAMES}
    write_json(out/'protocol.json',dict(code_sha256=code,producer_sources_sha256=producers,cases={n:dict(study=s.model_dump(mode='json'),request=p.model_dump(mode='json'),expected_first_derivative=e) for n,(s,p,e) in specs.items()},method='complete-study numerical derivative; analytic SI initialization, deadline and bound-assignment checks'))
    for name,(study,protocol,expected) in specs.items():
        r=differentiate_study(study,protocol);write_json(out/name/'result.json',r)
        passed=r.get('available',False)
        if passed:
            d=np.array(r['parameters'][0]['derivative']);wanted=np.array(pv_oracle()) if name=='pv_initialization' else np.array([expected])
            passed=bool(np.allclose(d[:len(wanted)],wanted,rtol=2e-4,atol=2e-6))
        row=dict(case=name,passed=passed);rows.append(row);print(name,passed,r.get('reason'),flush=True)
    if code!=package_fingerprint() or producers!={p.name:sha256_file(p) for p in Path(__file__).parent.glob('*.py')}:raise ValueError('sensitivity acceptance producer changed')
    result=dict(passed=all(r['passed'] for r in rows),rows=rows,code_sha256=code,analytic_global_adjoint=False,local_numerical_evidence=True,
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json'))})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);a=p.parse_args();raise SystemExit(0 if run(a.out)['passed'] else 1)
