"""Preregistered native-limiter closed feedback with an independent SI network."""
from pathlib import Path
import argparse,json,math,subprocess,sys
import numpy as np
from benchmarks.generator_oel2c_cases import example,NAMES
from benchmarks.native_online_validation import source_identity
from benchmarks.andes_joint_double_cage import sample_native
from benchmarks.generator_thermal_validation import physical_contract
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.generator_oel2c import CHANNELS
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.topology import compile_topology
from power_grid_env.report import write_json,sha256_file

TOLERANCES=(1e-8,1e-10,1e-12)
PHYSICAL={'source_voltage','motor_open','motor_close','generator_open','governor_step','voltage_reference_step','generator_field_thermal_trip','generator_thermal_trip'}
NEGATIVE=('unknown','duplicate','classical','no_exciter','mixed_OEL','field_base','startup_loaded','initial_reference')


def negative(name):
    d=example('hold').model_dump(mode='json');s=d['coupled']
    if name=='unknown':s['oel2c_limiters'][0]['generator_id']='missing'
    elif name=='duplicate':s['oel2c_limiters']*=2
    elif name=='classical':s['machines'][1]['model']='GENCLS'
    elif name=='no_exciter':s['machines'][1]['exciter']=None
    elif name=='mixed_OEL':s['overexcitation_limiters']=[dict(generator_id='g2',rated_field_reaction_pu=1.5,current_limit_pu=1.4,measurement_time_s=.04,feedback_gain_pu=.3,bias_time_s=.08,maximum_voltage_bias_pu=.15)]
    elif name=='field_base':s['generator_field_thermal']=[dict(generator_id='g2',rated_field_reaction_pu=1.,time_constant_s=.2,trip_level=.3)]
    elif name=='startup_loaded':
        from benchmarks.generator_excitation_cases import example as old
        s['operating_point']=old('hold').coupled.operating_point.model_dump(mode='json')
    elif name=='initial_reference':s['oel2c_limiters'][0]['ILimPu']=1.5
    else:raise ValueError('unknown negative OEL2C case')
    return d


def physical_events(report):return [e for e in report['event_log'] if e['kind'] in PHYSICAL]


def compare(spec,primary,native):
    if not primary.get('completed') or not native.get('completed'):raise ValueError('complete feedback traces required')
    left,right=physical_events(primary),physical_events(native)
    signature=lambda e:(e['kind'],e['equipment_id'])
    if [signature(e) for e in left]!=[signature(e) for e in right]:raise ValueError('independent physical event sequence differs')
    error=max((abs(a['time_s']-b['time_s']) for a,b in zip(left,right)),default=0.)
    mapping={a['time_s']:b['time_s'] for a,b in zip(left,right)};physical=set(mapping);step=spec.coupled.step_s;selected=[];times=[];sides=[]
    for i,(at,side) in enumerate(zip(primary['time_s'],primary['sample_side'])):
        on_grid=abs(at-round(at/step)*step)<=64*np.finfo(float).eps*max(1.,abs(at)) or abs(at-spec.coupled.duration_s)<1e-13
        if at in physical and side in ('pre_event','post_event'):
            selected.append(i);times.append(mapping[at]);sides.append(side)
        elif on_grid and side!='pre_event':selected.append(i);times.append(at);sides.append('regular')
    if not selected:raise ValueError('missing declared comparison grid')
    errors={};limits={}
    for key,values in native['channels'].items():
        if key not in primary['channels']:continue
        reference=np.asarray(primary['channels'][key])[selected];measured=sample_native(native,times,sides,key)
        if not np.isfinite(reference).all():raise ValueError('nonfinite primary reference')
        discrete=key.endswith(('.connected','.enable_timer_active','.enable_timer_expired','.reset_timer_active','.reset_timer_expired','.field_thermal_latched','.field_voltage_enabled','.stator_thermal_latched'))
        limit=0. if discrete else 100. if key.endswith(('.p_w','.q_var')) else 1e-6 if key.endswith(('_start_s','.field_thermal_level','.stator_thermal_level')) else 1e-5
        errors[key]=float(max(abs(reference-measured)));limits[key]=limit
    required={p.generator_id+'.OEL2C.'+k for p in spec.coupled.oel2c_limiters for k in (*CHANNELS,'field_reaction_pu','field_current_input_pu')}
    required.update(g.id+'.'+k for g in spec.network.generators for k in ('delta','omega','p_w','q_var','current_pu','e1q','e1d','e2d','e2q','psi2d','psi2q','Se','field_pu','TGOV1.valve_pu','TGOV1.turbine_state_pu','TGOV1.output_pu','SEXS.field_pu','SEXS.filter_pu','connected','terminal_voltage_pu'))
    if not required<=errors.keys():raise ValueError('missing native OEL2C channels')
    nodes={n.id for n in spec.network.connectivity_nodes}
    if set(native['node_voltage'])!=nodes:raise ValueError('native node inventory mismatch')
    ports=compile_topology(spec.network).node_to_bus;levels={v.id:v.nominal_ll_v/math.sqrt(3) for v in spec.network.voltage_levels}
    for node in spec.network.connectivity_nodes:
        bus=ports[node.id];target=np.asarray(primary['channels'][bus+'.v'])[selected]*np.exp(1j*np.asarray(primary['channels'][bus+'.a'])[selected])
        for part,expected in [('real_v',target.real),('imag_v',target.imag)]:
            trace=dict(time_s=native['time_s'],sample_side=native['sample_side'],channels={'v':native['node_voltage'][node.id][part]})
            measured=sample_native(trace,times,sides,'v')/levels[node.voltage_level_id]
            key='node.'+node.id+'.'+part;errors[key]=float(max(abs(expected-measured)));limits[key]=1e-5
    physical_check=physical_contract(spec,primary)
    passed=bool(error<=1e-6 and all(v<=limits[k] for k,v in errors.items()) and physical_check['passed'])
    return dict(passed=passed,errors=errors,limits=limits,maximum_physical_event_time_error_s=error,physical=physical_check,
                compared_samples=len(selected),sampling='authored export grid and independently matched physical event sides; internal limiter events qualified separately',
                native_limiter_in_closed_feedback=True,whole_stock_Dynawo_network_qualified=False)


def native_reference(spec,out,root,tolerance):
    from benchmarks.oel2c_model_exchange import scope
    scope(spec)
    out=Path(out);request=out.parent/(out.name+'-request.json');write_json(request,spec.model_dump(mode='json'))
    result=out.parent/(out.name+'-result.json');log=out.parent/(out.name+'-worker.log')
    with log.open('w') as stream:
        proc=subprocess.run([sys.executable,'-m','benchmarks.generator_oel2c_validation','--worker',str(request),'--out',str(out),'--root',str(root),'--tolerance',str(tolerance),'--result',str(result)],stdout=stream,stderr=subprocess.STDOUT,timeout=300)
    if proc.returncode or not result.exists():raise ValueError('native OEL2C worker failed: '+str(log))
    r=json.loads(result.read_text())
    if not r.get('completed'):raise ValueError('native OEL2C failed: '+str(r))
    return r


def run(out,root,names=NAMES):
    if not names or len(names)!=len(set(names)) or any(n not in NAMES for n in names):raise ValueError('unique registered OEL2C cases required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=source_identity()
    from benchmarks.dynawo_reference_setup import verify
    identity=verify(root)
    write_json(out/'protocol.json',dict(cases={n:canonical_hash(example(n)) for n in names},negative_inputs={n:negative(n) for n in NEGATIVE},tolerances=list(TOLERANCES),code_sha256=code,source_sha256=sources,
        state_limit=1e-5,power_limit_w_var=100.,physical_event_limit_s=1e-6,clock_and_heat_limit=1e-6,discrete_limit=0.,reference='native guarded Dynawo OEL2C ME + independent SI plant / CVODE',
        node_complex_voltage_limit_pu=1e-5,sampling='authored grid and physical event sides only; no primary internal event clocks',native_before_primary=True,reference_identity=identity))
    checks=[];write_json(out/'summary.json',dict(passed=False,status='running'))
    for name in names:
        print(name,flush=True);s=example(name);folder=out/name;folder.mkdir();write_json(folder/'study.json',s.model_dump(mode='json'));refs=[]
        for j,tol in enumerate(TOLERANCES):
            ref=native_reference(s,folder/('native-'+str(j)),root,tol)
            if ref['reference']!=identity:raise ValueError('native dependency identity changed')
            refs.append(ref)
        primary=run_study(s,isolate=True,wall_timeout_s=120.);write_json(folder/'production.json',primary)
        if not primary.get('completed'):raise ValueError('primary OEL2C case incomplete: '+name)
        rows=[compare(s,primary,r) for r in refs]
        keys=[k for k in rows[-1]['errors'] if k.endswith(('.delta','.omega','.speed_pu','.OEL2C.output_pu'))]
        converged=all(rows[-1]['errors'][k]<=rows[0]['errors'][k] or rows[-1]['errors'][k]<1e-8 for k in keys)
        check=dict(passed=rows[-1]['passed'] and converged,steps=rows,convergence_passed=converged);write_json(folder/'comparison.json',check)
        if not check['passed']:raise ValueError('closed-feedback comparison failed: '+name)
        checks.append(check)
    rejected={}
    for name in NEGATIVE:
        data=negative(name);write_json(out/'negative'/(name+'-input.json'),data)
        try:s=StudySpec.model_validate(data)
        except ValueError:rejected[name]='schema_rejected';continue
        r=run_study(s,isolate=True,wall_timeout_s=120.);write_json(out/'negative'/(name+'-result.json'),r)
        if r.get('completed') or 'channels' in r:raise ValueError('invalid OEL2C input accepted')
        rejected[name]=r['reason']
    if code!=package_fingerprint() or sources!=source_identity() or identity!=verify(root):raise ValueError('producer changed during OEL2C validation')
    result=dict(passed=True,cases=len(checks),native_closed_feedback_runs=len(checks)*3,rejected=rejected,code_sha256=code,
        maximum_physical_event_time_error_s=max(c['steps'][-1]['maximum_physical_event_time_error_s'] for c in checks),
        artifacts_sha256={str(p.relative_to(out)):sha256_file(p) for p in out.rglob('*') if p.is_file() and p!=out/'summary.json'})
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));p.add_argument('--worker',type=Path);p.add_argument('--result',type=Path);p.add_argument('--tolerance',type=float,default=1e-10);a=p.parse_args()
    if a.worker:
        from benchmarks.oel2c_model_exchange import run as execute
        try:r=execute(StudySpec.model_validate_json(a.worker.read_text()),a.out,a.root,a.tolerance)
        except Exception as e:r=dict(completed=False,status='native_oel2c_reference_failed',error=str(e))
        write_json(a.result,r);raise SystemExit(0 if r['completed'] else 1)
    print(json.dumps(run(a.out,a.root,tuple(a.cases)),indent=2))
