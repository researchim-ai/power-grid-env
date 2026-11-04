"""One native FMU per passport; independent constant-input witness states."""
from pathlib import Path
import sys,math,copy
import numpy as np
from benchmarks.dynawo_reference_setup import verify
from benchmarks.oel2c_model_exchange import NativeBlock
from benchmarks.oel2c_boundary_reference import probe_points,check_neighborhood
from benchmarks.oel2c_selected_reference import _candidate
from power_grid_env.report import write_json


def run(p,input0,out,root):
    root=Path(root).resolve();identity=verify(root);out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    if str(root/'fmpy') not in sys.path:sys.path.insert(0,str(root/'fmpy'))
    from fmpy.fmi2 import FMU2Model
    import fmpy
    if fmpy.__version__!=identity['fmpy_version'] or not Path(fmpy.__file__).resolve().is_relative_to(root/'fmpy'):raise ValueError('wrong native FMI runtime')
    audit=check_neighborhood(p,input0)
    if not audit['passed']:raise ValueError('native component neighborhood is sensitive')
    block=NativeBlock(p,input0,out/'native',root,.5,1e-12,FMU2Model);rows=[]
    try:
        for point in probe_points(p,input0):
            states,origin=_candidate(p,point)
            if abs(origin+block.time_offset)>1e-12:raise ValueError('witness changes native clock history')
            actual=copy.deepcopy(states);ref='limiter.oelReferenceCurrent.limIntegrator1.y';timer='limiter.oelReferenceCurrent.limIntegrator.y'
            if actual[ref] in (p.ILimPu,p.IInstPu):actual[ref]=math.nextafter(actual[ref],math.inf if actual[ref]==p.IInstPu else -math.inf)
            if actual[timer]==p.tMin:actual[timer]=math.nextafter(p.tMin,-math.inf)
            elif actual[timer]==p.tMax:actual[timer]=math.nextafter(p.tMax,math.inf)
            x=np.array([actual[k] for k in block.native_state_names]);block.f.setReal([block.real['fieldCurrent']],[point]);block.f.setContinuousStates(np.ctypeslib.as_ctypes(x),block.nx)
            indicators=np.zeros(block.nz);block.f.getEventIndicators(np.ctypeslib.as_ctypes(indicators),block.nz);block.update()
            derivative=np.zeros(block.nx);block.f.getDerivatives(np.ctypeslib.as_ctypes(derivative),block.nx)
            residual=float(max(abs(derivative)))
            if not np.isfinite(derivative).all() or residual>1e-8:raise ValueError('native witness is not stationary')
            rows.append(dict(input_current_pu=point,native_derivative_per_s=residual,channels=block.signals(0.)))
    finally:block.close()
    result=dict(passed=True,reference=identity,independent_native_initial_states=True,production_RHS_used=False,interval_certified=False,probes=len(rows),witnesses=rows,guard_audit=audit)
    write_json(out/'native-witnesses.json',result);return result


def compare_primary(p,result):
    # Production equations enter comparison only, never native construction.
    from power_grid_env.generator_oel2c import OEL2CDynamics,CHANNELS
    local=p.model_copy(update=dict(equilibrium_boundary_policy='native_relations',equilibrium_probe_radius_pu=None));errors=[]
    for row in result['witnesses']:
        k=OEL2CDynamics(local);x=k.initial(row['input_current_pu']);primary=k.signals(x);native=row['channels']
        if set(primary)!=set(CHANNELS) or set(native)!=set(primary):raise ValueError('component witness channel inventory differs')
        for name in CHANNELS:
            limit=0. if name.endswith(('_active','_expired')) else 1e-6 if name.endswith('_start_s') else 1e-8
            error=abs(primary[name]-native[name])
            if not math.isfinite(error) or error>limit:raise ValueError('native witness channel mismatch: '+name)
            errors.append(error)
    return dict(passed=True,probes=len(result['witnesses']),maximum_channel_error=max(errors,default=0.),native_derivative_limit_per_s=1e-8,interval_certified=False)

COMPONENTS=('scaled','contact','plateau','inverse_zero','scaled_gain','cubic_scaled','neutral_PID_upper','neutral_PID_lower','roundoff_release')


def example(name):
    from benchmarks.generator_oel2c_boundary_cases import data
    from power_grid_env.generator_oel2c import OEL2CPassport
    if name not in COMPONENTS:raise ValueError(name)
    kind='roundoff' if name=='roundoff_release' else name if name in ('contact','plateau') else 'scaled';params=data(kind);point=1.25 if kind in ('contact','plateau','roundoff') else 1.
    if kind=='scaled':params['ITfPu']=1.
    if name=='inverse_zero':params.update(Sw1=True,K1=0.)
    if name=='scaled_gain':params.update(KScale=2.,KAct=.5);point=.5
    if name=='cubic_scaled':params.update(C2=3.)
    if name.startswith('neutral_PID'):
        params.update(equilibrium_reference_current_pu=.8,equilibrium_pid_output_pu=0. if name.endswith('upper') else -.4,equilibrium_probe_radius_pu=1e-12);point=.8
    return OEL2CPassport(**params),point


def qualify(out,root,names=COMPONENTS):
    from power_grid_env.evidence import package_fingerprint
    from benchmarks.native_online_validation import source_identity
    from power_grid_env.report import sha256_file
    if not names or len(set(names))!=len(names) or not set(names)<=set(COMPONENTS):raise ValueError('unique registered component cases required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();source=source_identity();identity=verify(root)
    write_json(out/'protocol.json',dict(code_sha256=code,source_sha256=source,reference=identity,cases={n:dict(passport=example(n)[0].model_dump(mode='json'),input_current_pu=example(n)[1]) for n in names},native_residual_limit_per_s=1e-8,continuous_channel_limit=1e-8,clock_epoch_limit_s=1e-6,discrete_channel_limit=0.,interval_certified=False))
    write_json(out/'summary.json',dict(passed=False,status='running'));rows={}
    for name in names:
        print(name,flush=True);p,point=example(name);r=run(p,point,out/name,root);check=compare_primary(p,r);write_json(out/name/'comparison.json',check);rows[name]=check
    if code!=package_fingerprint() or source!=source_identity() or identity!=verify(root):raise ValueError('component producer changed')
    result=dict(passed=True,cases=len(rows),native_FMUs=len(rows),native_witness_states=sum(v['probes'] for v in rows.values()),code_sha256=code,interval_certified=False,hybrid_stability_qualified=False,artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in out.rglob('*') if p.is_file() and p!=out/'summary.json'})
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--cases',nargs='+',choices=COMPONENTS,default=list(COMPONENTS));a=p.parse_args();print(json.dumps(qualify(a.out,a.root,tuple(a.cases)),indent=2))
