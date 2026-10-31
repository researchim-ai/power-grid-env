"""Independent SI-winding/GENROU qualification corpus; synthetic, not field data."""
from pathlib import Path
import argparse
import numpy as np
from benchmarks.genrou_coupled_reference import reference
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.report import write_json, sha256_file
from power_grid_env.evidence import package_fingerprint

NAMES=('cold_start','loaded_sag','setpoint_steps','saturated_sag','restart')


def fixture(name):
    if name not in NAMES:raise ValueError('unknown joint reference case')
    base={'cold_start':'hold','loaded_sag':'voltage_sag','restart':'voltage_sag'}.get(name,name)
    spec=StudySpec.model_validate_json(Path('cases/studies/genrou_coupled_'+base+'.json').read_text())
    spec.id='genrou-double-cage-'+name
    m=spec.coupled.motors[0];m.model='double_cage';m.rotor2_r_pu=.06;m.rotor2_x_pu=.2;m.rotor_mutual_x_pu=.03
    if name=='cold_start':
        spec.coupled.operating_point=None
        # Authored source machine rating supports the motor locked-rotor var demand.
        spec.coupled.machines[0].rated_va=8e6
    if name=='restart':
        data=spec.model_dump(mode='json');data['coupled']['events']=[
            dict(time_s=.2,kind='motor_open',equipment_id=m.id),
            dict(time_s=.5,kind='motor_close',equipment_id=m.id)]
        spec=StudySpec.model_validate(data)
    return spec


def compare(spec,out=None):
    actual=run_study(spec)
    if not actual.get('completed'):raise RuntimeError(actual)
    expected=reference(spec,actual)
    errors={k:float(np.max(abs(np.array(v)-actual['channels'][k]))) for k,v in expected['channels'].items()}
    # Same preregistered limits as the existing single-cage joint SI corpus.
    limits={k:100. if k.endswith(('.p_w','.q_var')) else 1e-5 if k.endswith(('.omega','.speed_pu','.delta')) else 1e-4 for k in errors}
    passed=all(np.isfinite(errors[k]) and errors[k]<=limits[k] for k in errors)
    result=dict(case_id=spec.id,passed=passed,maximum_errors=errors,limits=limits,
                samples=len(actual['time_s']),field_validated=False,industrial_profile_qualified=False)
    if out is not None:
        out=Path(out);write_json(out/'study.json',spec.model_dump(mode='json'))
        write_json(out/'production.json',actual)
        write_json(out/'independent.json',dict(time_s=actual['time_s'],sample_side=actual['sample_side'],**expected))
        result['artifacts_sha256']={name:sha256_file(out/name) for name in ['study.json','production.json','independent.json']}
        write_json(out/'comparison.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    rows=[compare(fixture(name),a.out/name) for name in NAMES]
    result=dict(passed=all(r['passed'] for r in rows),cases=rows,code_sha256=package_fingerprint(),
        sources={str(p):sha256_file(p) for p in map(Path,[__file__,'benchmarks/genrou_coupled_reference.py','benchmarks/induction_reference.py','benchmarks/genrou_reference.py'])},
        input_cases_sha256={str(p):sha256_file(p) for p in [Path('cases/studies/genrou_coupled_'+name+'.json') for name in ['hold','voltage_sag','setpoint_steps','saturated_sag']]},
        method='SI stator/two rotor/generator simultaneous circuit, physical winding flux, independent GENROU dq initialization and scipy.signal controls, DOP853; production uses reduced EMF/Radau',
        scope='One source, transposed uncharged feeder, common motor/generator bus, TGOV1/SEXS; no PSS, contactor automation, topology changes or EMT',
        external_simulator_joint_qualified=False,field_validated=False,industrial_profile_qualified=False)
    write_json(a.out/'summary.json',result)
    print([(r['case_id'],r['passed']) for r in rows])
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
