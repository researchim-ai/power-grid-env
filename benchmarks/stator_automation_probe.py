"""Standalone installed-runtime probe for the generator-current temperature partial.

Run with Python -I. Only installed runtime and owner JSON are imported/read;
there are no checkout or reference module imports.
"""
import argparse
import sys
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--installed-dir',required=True)
p.add_argument('--study',required=True);p.add_argument('--out',required=True)
a=p.parse_args();installed=Path(a.installed_dir).resolve();sys.path.insert(0,str(installed))
import numpy as np
from power_grid_env.study_spec import StudySpec
from power_grid_env.coupled_rms import CoupledNetwork
from power_grid_env.coupled_initialization import initialize_coupled
from power_grid_env.automation_jacobian import current_directions
from power_grid_env.study_artifacts import read_json,encoded,digest
from power_grid_env.evidence import package_fingerprint

study=read_json(a.study);spec=StudySpec.model_validate(study);m=CoupledNetwork(spec.network,spec.coupled)
x,audit=initialize_coupled(m)
if x is None:raise ValueError('probe initialization failed')
bank=m.automation;r=next(r for r in bank.relays if r.measurement_kind=='generator')
address=m.generator_heat.entries[0][0];x[address]+=30.
u=np.zeros((len(x),1));u[address]=1.;v=m.electrical(x)[0];dv=np.zeros((len(v),1),complex);h=1e-4
def current(y):return bank.port('generator',r.measurement_id,y,v)[1]
expected=(current(x+h*u[:,0])-current(x-h*u[:,0]))/(2*h)
actual=current_directions(bank,r,x,u,dv)[0];error=float(abs(actual-expected))
if not np.isfinite(error) or abs(expected)<.01:raise ValueError('probe lacks measurable temperature coupling')
for name,module in sys.modules.items():
    if name.startswith('power_grid_env') and getattr(module,'__file__',None) and not Path(module.__file__).resolve().is_relative_to(installed):
        raise ValueError('mixed installed/checkout imports')
result=dict(code_sha256=package_fingerprint(),study_sha256=digest(encoded(study)),
    probe_sha256=digest(Path(__file__).read_bytes()),actual_a_per_k=[float(actual.real),float(actual.imag)],
    finite_difference_a_per_k=[float(expected.real),float(expected.imag)],absolute_error_a_per_k=error,
    passed=error<1e-9,temperature_increment_c=30.,difference_step_c=h,independent_host=False)
out=Path(a.out)
if out.exists():raise ValueError('fresh probe output required')
out.write_bytes(encoded(result));print(result)
