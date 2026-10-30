"""Loaded operating points and disturbances versus physical SI flux references."""
from pathlib import Path
import argparse
import numpy as np
from benchmarks.induction_reference import rotor_flux_group_reference
from benchmarks.thermal_motor_reference import reference as thermal_reference
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.report import write_json, sha256_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for name in ('fan_hold', 'mixed_sag', 'load_step', 'group_sag', 'thermal_hold', 'contactor_restart'):
        path = Path('cases/studies/operating_'+name+'.json')
        spec = StudySpec.model_validate_json(path.read_text())
        report = run_study(spec)
        if not report.get('completed'):
            raise RuntimeError(report)
        references = rotor_flux_group_reference(spec, report)
        keys = ['speed_pu', 'e_real_pu', 'e_imag_pu', 'current_pu', 'bus_voltage_pu', 'p_w', 'q_var']
        limits = dict(zip(keys, [2e-5, 2e-5, 2e-5, 1e-4, 1e-5, 100., 100.]))
        errors = {}
        for motor in spec.induction.motors:
            actual = np.column_stack([report['channels'][motor.id+'.'+key] for key in keys])
            errors[motor.id] = dict(zip(keys, np.max(abs(actual-references[motor.id]), axis=0).tolist()))
        thermal_error = None
        if name == 'thermal_hold':
            independent = thermal_reference(spec, report)
            thermal_error = float(max(abs(independent['values'][:, 1]-report['channels']['m.thermal_level'])))
        passed = all(row[key] <= limits[key] for row in errors.values() for key in keys) and (thermal_error is None or thermal_error <= 2e-5)
        write_json(args.out/(name+'.json'), report)
        write_json(args.out/(name+'-reference.json'), dict(time_s=report['time_s'], sample_side=report['sample_side'],
                   channels={motor: dict(zip(keys, values.T.tolist())) for motor, values in references.items()}))
        rows.append(dict(case=str(path), case_sha256=sha256_file(path), maximum_errors=errors, limits=limits,
                         thermal_error=thermal_error, thermal_tolerance=2e-5, passed=passed))
    write_json(args.out/'reference.json', dict(cases=rows,
               reference_sha256=sha256_file(Path('benchmarks/induction_reference.py')),
               thermal_reference_sha256=sha256_file(Path('benchmarks/thermal_motor_reference.py')),
               benchmark_sha256=sha256_file(Path(__file__)),
               method='Independent full physical T-circuit/shaft equilibrium and SI rotor-flux DOP853',
               field_validated=False, industrial_profile_qualified=False))
    print([(r['case'], r['passed'], max(e['speed_pu'] for e in r['maximum_errors'].values())) for r in rows])
    if not all(row['passed'] for row in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
