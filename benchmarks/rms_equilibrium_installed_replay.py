"""Standalone equilibrium/refusal Python-I installed-wheel replay; no checkout/reference imports."""
import argparse
import importlib.abc
import sys
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--installed-dir', required=True)
p.add_argument('--data-dir', required=True)
a = p.parse_args()
installed, root = Path(a.installed_dir).resolve(), Path(a.data_dir).resolve()
sys.path.insert(0, str(installed))


class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'benchmarks', 'tests', 'motulator', 'andes'}:
            raise ImportError('checkout/reference import forbidden')


sys.meta_path.insert(0, Block())
import numpy as np
from power_grid_env.study_spec import StudySpec, run_study
from power_grid_env.phase_research import PhaseStudy, evaluate
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json, encoded, seal
import power_grid_env.phase_network as runtime

if not Path(runtime.__file__).resolve().is_relative_to(installed):
    raise ValueError('runtime imported from outside installed wheel')
rows = []
for record in read_json(root/'records.json'):
    if record['kind'] == 'phase':
        result = evaluate(PhaseStudy.model_validate(record['study']))
    else:
        result = run_study(StudySpec.model_validate(record['study']))
    if not result.get('completed'):
        expected=record.get('expected',{})
        if expected.get('completed') is not False or result.get('status')!=expected.get('status') or result.get('reason')!=expected.get('reason'):
            raise ValueError('installed replay failed or refusal differs')
        child=root/record['id'];child.mkdir()
        for key,value in [('study',record['study']),('trace',result)]:
            (child/(key+'.json')).write_bytes(encoded(value))
        seal(child)
        rows.append(dict(id=record['id'],passed=True,expected_initial_rejection=True,reason=result.get('reason')))
        print('installed equilibrium refusal',record['id'],flush=True)
        continue
    previous = record.get('expected')
    largest = 0.
    exact = True
    if previous is not None:
        for key in ['time_s', 'sample_side', 'event_log']:
            if result[key] != previous[key]:
                raise ValueError('installed event/axis mismatch: '+record['id']+'/'+key)
        if set(previous['channels']) != set(result['channels']):
            if not record.get('allow_added_thermal_diagnostics') or not set(previous['channels']) <= set(result['channels']):
                raise ValueError('installed channel inventory mismatch')
            for key in set(result['channels'])-set(previous['channels']):
                if not key.endswith(('_resistance_ohm', '_net_heat_w', '.ambient_heat_w', '.cross_heat_w', '_temperature_c')):
                    raise ValueError('unexpected new channel')
        for key, values in previous['channels'].items():
            x, y = np.array(result['channels'][key]), np.array(values)
            limit = 1e-12+1e-12*np.maximum(abs(x), abs(y))
            if x.shape != y.shape or not np.all(np.isfinite(x)) or not np.all(abs(x-y) <= limit):
                raise ValueError('installed numerical mismatch: '+record['id']+'/'+key)
            largest = max(largest, float(max(abs(x-y)/limit)))
            exact &= bool(np.array_equal(x, y))
    child = root/record['id']
    child.mkdir()
    for key, value in [('study', record['study']), ('trace', result)]:
        (child/(key+'.json')).write_bytes(encoded(value))
    seal(child)
    rows.append(dict(id=record['id'], passed=True, compared=previous is not None,
                     common_channels_bitwise_exact=exact if previous is not None else None, maximum_tolerance_ratio=largest))
    print('installed electrothermal', record['id'], flush=True)
if any(not Path(m.__file__).resolve().is_relative_to(installed) for name, m in sys.modules.items() if name.startswith('power_grid_env') and getattr(m, '__file__', None)):
    raise ValueError('mixed installed/checkout runtime')
receipt = dict(passed=True, code_sha256=package_fingerprint(), runtime_file=runtime.__file__, rows=rows,
               absolute_tolerance=1e-12, relative_tolerance=1e-12, reference_imports_blocked=True, independent_host=False,
               scope='Installed wheel outside checkout, Python-I, same host/shared dependencies. Native event/axis identity and channel roundoff comparison where baseline supplied. Import guard is not OS sandbox or authentication.')
(root/'receipt.json').write_bytes(encoded(receipt))
seal(root)
