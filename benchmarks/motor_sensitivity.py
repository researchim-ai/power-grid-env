"""Seven frozen perturbations checked with independent physical SI trajectories."""
from pathlib import Path
import argparse
import json
import numpy as np
from benchmarks.induction_reference import rotor_flux_group_reference
from power_grid_env.motor_sensitivity import MotorSensitivityProtocol,run_motor_sensitivity
from power_grid_env.study_spec import StudySpec
from power_grid_env.report import write_json,sha256_file


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    base_path=Path('cases/studies/operating_mixed_sag.json')
    protocol_path=Path('cases/sensitivity/loaded_motor_sag.json')
    base=StudySpec.model_validate_json(base_path.read_text())
    protocol=MotorSensitivityProtocol.model_validate_json(protocol_path.read_text())
    result=run_motor_sensitivity(base,protocol,args.out/'experiment')
    if not result['completed']:
        raise RuntimeError(result['status'])
    checks=[]
    keys=['speed_pu','e_real_pu','e_imag_pu','current_pu','bus_voltage_pu','p_w','q_var']
    limits=dict(zip(keys,[2e-5,2e-5,2e-5,1e-4,1e-5,100.,100.]))
    for row in result['cases']:
        directory=args.out/'experiment'/row['id']
        spec=StudySpec.model_validate_json((directory/'study.json').read_text())
        raw=json.loads((directory/'result.json').read_text())
        # Independently verify the experimental transformation, not only
        # consistency of the solvers after a possibly incorrect mutation.
        m,b=spec.induction.motors[0],base.induction.motors[0]
        f=row['factors']
        mapping=(abs(m.inertia_s-b.inertia_s*f['inertia'])<1e-12 and
                 abs(m.rotor_r_pu-b.rotor_r_pu*f['rotor'])<1e-12 and
                 abs(spec.network.sources[0].r_ohm-base.network.sources[0].r_ohm*f['grid'])<1e-12 and
                 abs(spec.network.sources[0].x_ohm-base.network.sources[0].x_ohm*f['grid'])<1e-12)
        expected=rotor_flux_group_reference(spec,raw)['m']
        actual=np.column_stack([raw['channels']['m.'+key] for key in keys])
        errors=dict(zip(keys,np.max(abs(actual-expected),axis=0).tolist()))
        checks.append(dict(case=row['id'],parameter_mapping_correct=mapping,maximum_errors=errors,limits=limits,
                           passed=mapping and all(errors[k]<=limits[k] for k in keys)))
    write_json(args.out/'reference.json',dict(cases=checks,base_study_sha256=sha256_file(base_path),
        protocol_file_sha256=sha256_file(protocol_path),benchmark_sha256=sha256_file(Path(__file__)),
        independent_reference_sha256=sha256_file(Path('benchmarks/induction_reference.py')),
        method='Independent T-circuit equilibrium and SI flux DOP853 at every declared parameter point',
        field_validated=False,industrial_profile_qualified=False))
    print([(c['case'],c['passed'],c['maximum_errors']['speed_pu']) for c in checks])
    if not all(c['passed'] for c in checks):
        raise SystemExit(1)


if __name__=='__main__':
    main()
