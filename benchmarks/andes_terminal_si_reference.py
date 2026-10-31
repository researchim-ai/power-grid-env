"""Independent SI winding-flux/GENROU triangulation of the terminal restart corpus."""
from pathlib import Path
import argparse
import numpy as np
from power_grid_env.study_spec import run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json,sha256_file
from benchmarks.andes_terminal_restart import example,NAMES
from benchmarks.genrou_coupled_reference import reference


def compare(spec,out=None):
    actual=run_study(spec)
    if not actual.get('completed'):raise ValueError('production restart did not complete')
    independent=reference(spec,actual)
    errors={key:float(max(abs(np.array(values)-actual['channels'][key]))) for key,values in independent['channels'].items()}
    limits={key:100. if key.endswith(('.p_w','.q_var')) else 1e-5 if key.endswith(('.omega','.speed_pu','.delta')) else 1e-4 for key in errors}
    result=dict(case_id=spec.id,passed=all(np.isfinite(errors[key]) and errors[key]<=v for key,v in limits.items()),maximum_errors=errors,limits=limits,field_validated=False,industrial_profile_qualified=False)
    if out is not None:
        out=Path(out);write_json(out/'study.json',spec.model_dump(mode='json'));write_json(out/'production.json',actual)
        write_json(out/'independent.json',dict(time_s=actual['time_s'],sample_side=actual['sample_side'],**independent))
        result['artifacts_sha256']={p.name:sha256_file(p) for p in out.glob('*.json') if p.name!='comparison.json'}
        write_json(out/'comparison.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    write_json(a.out/'summary.json',dict(passed=False,status='running'))
    rows=[]
    try:
        for name in NAMES:
            row=compare(example(name),a.out/name);rows.append(row);print(name,row['passed'],flush=True)
        result=dict(passed=all(r['passed'] for r in rows),cases=rows,code_sha256=package_fingerprint(),
            source_sha256={str(p):sha256_file(p) for p in map(Path,[__file__,'benchmarks/genrou_coupled_reference.py','benchmarks/genrou_reference.py','benchmarks/induction_reference.py'])},
            method='SI stator/two rotor/generator simultaneous circuit, rotor flux, independent GENROU dq and scipy.signal transfer blocks, DOP853; no production constants, initialization or RHS',field_validated=False,industrial_profile_qualified=False)
    except Exception as exc:
        write_json(a.out/'summary.json',dict(passed=False,status='failed',error=str(exc)[:2000],cases=rows));raise
    write_json(a.out/'summary.json',result)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
