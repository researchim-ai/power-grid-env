"""Reproducible GENROU modal check against independent ODE differences.

Run: python3 -m benchmarks.small_signal_reference --out reports/small-signal-2026-10-30
"""
from pathlib import Path
import argparse
import numpy as np
from scipy.optimize import linear_sum_assignment
from benchmarks.genrou_reference import independent_reference
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.report import write_json, sha256_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for name in ('genrou', 'genrou_saturated'):
        path = Path('cases/studies/small_signal_' + name + '.json')
        spec = StudySpec.model_validate_json(path.read_text())
        result = run_study(spec)
        if not result.get('completed'):
            raise RuntimeError(result)
        reference = independent_reference(spec, [], linearize=True)
        expected = np.linalg.eigvals(reference)
        actual = np.array([complex(m['real_per_s'], m['imag_rad_per_s']) for m in result['modes']])
        i, j = linear_sum_assignment(abs(actual[:, None] - expected))
        error = float(max(abs(actual[i] - expected[j])))
        write_json(args.out / (name + '.json'), result)
        rows.append(dict(case=str(path), case_sha256=sha256_file(path),
                         independent_matrix_per_s=reference.tolist(),
                         independent_eigenvalues=[[float(x.real), float(x.imag)] for x in expected],
                         max_eigenvalue_error_per_s=error, tolerance_per_s=1e-5,
                         passed=error < 1e-5))
    write_json(args.out / 'reference.json', dict(
        cases=rows, reference_sha256=sha256_file(Path('benchmarks/genrou_reference.py')),
        benchmark_sha256=sha256_file(Path(__file__)),
        method='Independent six-state ODE central differences, step 1e-6; optimal eigenvalue matching',
        industrial_profile_qualified=False, field_validated=False))
    if not all(row['passed'] for row in rows):
        raise SystemExit(1)
    print('Passed:', [(r['case'], r['max_eigenvalue_error_per_s']) for r in rows])


if __name__ == '__main__':
    main()
