"""Python-I probe of authored-command priority at a rounded transfer deadline."""
import argparse
import sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--installed-dir',required=True);p.add_argument('--study',required=True);p.add_argument('--out',required=True)
a=p.parse_args();installed=Path(a.installed_dir).resolve();sys.path.insert(0,str(installed))
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.study_artifacts import read_json,encoded,digest
from power_grid_env.evidence import package_fingerprint
study=read_json(a.study);rows=[]
for method in ('radau','network_dae'):
    import copy
    s=copy.deepcopy(study);s['coupled']['integration_method']=method
    result=run_study(StudySpec.model_validate(s))
    if not result['completed']:raise ValueError('probe integration failed')
    chosen=[e for e in result['event_log'] if e['kind'] in ('relay_measurement_lost','breaker_close_requested')]
    expected=[('relay_measurement_lost',.075),('breaker_close_requested',.075)]
    rows.append(dict(method=method,passed=[(e['kind'],e['time_s']) for e in chosen]==expected,selected_events=chosen))
for name,module in sys.modules.items():
    if name.startswith('power_grid_env') and getattr(module,'__file__',None) and not Path(module.__file__).resolve().is_relative_to(installed):raise ValueError('mixed checkout/runtime imports')
result=dict(passed=all(r['passed'] for r in rows),code_sha256=package_fingerprint(),study_sha256=digest(encoded(study)),probe_sha256=digest(Path(__file__).read_bytes()),rows=rows,independent_host=False)
out=Path(a.out)
if out.exists():raise ValueError('fresh output required')
out.write_bytes(encoded(result));print(dict(passed=result['passed'],rows=rows))
