"""Registered local comparisons: own AC vs pandapower, RMS vs ANDES,
full windings vs original motulator + independent SI network.

Run from the source tree; third-party dependencies are optional and must already
exist locally. A missing dependency fails its required run. No downloads/CI.
The installed core package can verify the resulting portable data-only bundle.
"""
from pathlib import Path
import argparse
import importlib.metadata
import json
import math
import os
import subprocess
import sys
import time

import numpy as np

from power_grid_env.comparison import ComparisonPlan,ComparisonCase,ComparisonTrace,finalize_comparison
from power_grid_env.study_artifacts import encoded,digest,read_json,inventory
from power_grid_env.evidence import package_fingerprint
from power_grid_env.ipc import kill_process_group

ROOT=Path(__file__).resolve().parents[1]


def registered_plan():
    from power_grid_env.network_opf import OpfCase,Bus,Branch,GeneratorSpec
    from power_grid_env.balanced_ac import case_document
    from benchmarks.andes_motor_reference import LIMITS
    from benchmarks.flux_network_native import example
    cases=[]
    for mesh in [False,True]:
        buses=[Bus(10,3,0,0,0,0,1.04,0,.8,1.2),Bus(20,2 if mesh else 1,20,8,.2,0,1.01,0,.8,1.2),Bus(30,1,45,12,0,.2,1.,0,.8,1.2)]
        lines=[Branch(10,20,.02,.08,0.,1000,1.03,3.,1,-90,90),Branch(20,30,.04,.12,.01,1000,0,0,1,-90,90)]
        if mesh:lines.append(Branch(10,30,.03,.1,.01,1000,0,0,1,-90,90))
        generators=[GeneratorSpec(10,-1000,1000,-1000,1000,(0.,),pg=0)]
        if mesh:generators.append(GeneratorSpec(20,-1000,1000,0,1000,(0.,),pg=15))
        case=OpfCase('authored-meshed-pv' if mesh else 'authored-radial-tap',100,buses,generators,lines)
        cases.append(dict(id='ac-meshed-pv' if mesh else 'ac-radial-tap',track='steady_state',provider='pandapower_balanced',
            reference_kind='independent_implementation',reference_backend='pandapower',reference_version='3.5.6',sample_scope='steady_state',axis_quantity='bus_id',
            primary_axis=[10.,20.,30.],reference_axis=[10.,20.,30.],
            input=dict(case=case_document(case)),mappings=['Balanced positive-sequence MW/Mvar/MVA, pu branch impedances and pu bus shunt admittances; common 10 kV bus bases.',
                'Production bus gs/bs are pu admittances, converted to MATPOWER MW/Mvar at 1 pu by multiplying baseMVA.',
                'Tap/phase branch has zero shunt; ordinary lines retain charging. No capacitive transformer excitation approximation.',
                'Same MATPOWER bus/branch/tap/phase/shunt and PV setpoints; single slack; reactive limits disabled on both sides.',
                'Voltage magnitudes and slack-referenced angles at every bus; branch flows and Q-limit transitions are not compared. No OPF objective.'],
            channels=[dict(name='voltage_pu',unit='pu',absolute_limit=1e-8),dict(name='angle_rad',unit='rad',absolute_limit=1e-8)]))
    for name in ['nominal','nominal_dae','frequency_base','weak_grid']:
        s=read_json(ROOT/('cases/studies/andes_motor_'+('frequency_base' if name=='frequency_base' else 'nominal')+'.json'))
        if name=='nominal_dae':s['induction'].update(integration_method='network_dae',jacobian_format='csc',jacobian_network='direct_solve')
        if name=='weak_grid':
            for key in ['r_ohm','x_ohm']:s['network']['lines'][0][key]=[[x*3 for x in row] for row in s['network']['lines'][0][key]]
        units={k:'W' if k=='p_w' else 'var' if k=='q_var' else 'pu' for k in LIMITS}
        cases.append(dict(id='rms-'+name.replace('_','-'),track='rms_motor',provider='andes_motor3',reference_kind='independent_implementation',
            reference_backend='ANDES Motor3',reference_version='2.0.0',alignment='linear_event_free',sample_scope='event_free_trajectory',
            primary_axis=physical_axis(s['induction']['duration_s'],s['induction']['step_s']),reference_axis=physical_axis(s['induction']['duration_s'],.000125),
            input=dict(study=s,native_step_s=.000125),mappings=['Cold slip=1 and rotor E=0, native fixed-X G=0; no steady loaded initialization.',
                'Motor and system MVA bases explicitly mapped. Native torque polynomial is mapped to fan coefficient times speed squared.',
                'Native Id/Iq and d/q rotor states mapped to declared production complex E convention; event-free interpolation only.',
                'Nominal Radau/DAE are two execution variants of one physical case, not independent topology families.'],
            channels=[dict(name=k,unit=units[k],absolute_limit=v) for k,v in LIMITS.items()]))
    for name in ['radial_pair','meshed_pair']:
        network,settings=example(name)
        # Reference emits its complete inventory. Declare it before execution
        # from the independently defined circuit output names, not a result.
        contracts=flux_inventory(network,settings)
        cases.append(dict(id='flux-'+name.replace('_','-'),track='flux_network',provider='motulator_network',reference_kind='independent_implementation',
            reference_backend='motulator + independent SI network',reference_version='0.19.0',sample_scope='right_event_limits',
            primary_axis=physical_axis(settings.duration_s,settings.step_s),reference_axis=physical_axis(settings.duration_s,settings.step_s),
            input=dict(network=network.model_dump(mode='json'),settings=settings.model_dump(mode='json')),
            mappings=['Same SI Gamma passports, radial/meshed RL circuit and constant-Z load; authored mechanical and second saturation coefficients.',
                'Production winding currents vs original motulator flux coordinates; independently assembled dense SI network.',
                'Right-continuous output samples only; no claim to compare pre-event limits or instantaneous continuous extrema.'],channels=contracts))
    for identifier,coverage,reason in [('qwen-motor-vlm','not_performed','Multimodal Qwen motor policy and held-out task profile are not qualified'),('general-emt','not_applicable','Unrestricted EMT is outside this registered balanced/motor subset')]:
        cases.append(dict(id=identifier,track='agent_workflow' if identifier.startswith('qwen') else 'phase_emt',provider='qwen_workflow' if identifier.startswith('qwen') else 'pscad_emt',coverage=coverage,exclusion_reason=reason,
            reference_kind='independent_implementation',reference_backend='not executed',reference_version='not executed',sample_scope='steady_state',
            input=dict(scope=reason),mappings=[reason],channels=[dict(name='task_success',unit='fraction',absolute_limit=.01)]))
    return ComparisonPlan.model_validate(dict(id='registered-cross-simulator-v1',cases=cases))


def physical_axis(duration,step):
    return [*np.arange(math.ceil(duration/step))*step,float(duration)]


def flux_inventory(network,settings):
    """Static counterpart of NativeCircuit.outputs; checked against actual output."""
    from benchmarks.flux_network_native import _bus,LIMITS
    contracts=[]
    def add(name,unit,limit):contracts.append(dict(name=name,unit=unit,absolute_limit=limit))
    # Actual names are checked below against the reference's complete inventory.
    for node in network.connectivity_nodes:
        for axis in ['alpha','beta']:add(_bus(node.id)+'.voltage_'+axis+'_v','V',LIMITS['voltage_v'])
    for line in network.lines:
        for axis in ['alpha','beta']:add(line.id+'.current_'+axis+'_a','A',LIMITS['current_a'])
    for source in network.sources:
        for axis in ['alpha','beta']:add(source.id+'.current_'+axis+'_a','A',LIMITS['current_a'])
    for load in network.loads:
        for axis in ['alpha','beta']:add(load.id+'.current_'+axis+'_a','A',LIMITS['current_a'])
    for p in settings.motors:
        for name in ['current_alpha_a','current_beta_a','cage1_current_alpha_a','cage1_current_beta_a']:
            add(p.id+'.'+name,'A',LIMITS['current_a'])
        for name in ['stator_flux_alpha_vs','stator_flux_beta_vs','cage1_flux_alpha_vs','cage1_flux_beta_vs']:
            add(p.id+'.'+name,'V s',LIMITS['flux_vs'])
        add(p.id+'.speed_rad_s','rad/s',LIMITS['speed_rad_s']);add(p.id+'.torque_nm','N m',LIMITS['torque_nm'])
        for name in ['stator_copper_w','rotor_copper_w']:add(p.id+'.'+name,'W',.02)
    return contracts


def source_files(paths):return {str(p):digest(Path(p).read_bytes()) for p in paths}


def benchmark_sources():
    return {p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in sorted((ROOT/'benchmarks').rglob('*.py'))}


def run_provider(case,out,wheel):
    from power_grid_env.balanced_ac import case_from_document,solve_power_flow
    from power_grid_env.study_spec import StudySpec,run_study
    case=ComparisonCase.model_validate(case);out=Path(out);code=package_fingerprint();identity=digest(encoded(case.input))
    primary_sources=source_files([ROOT/'power_grid_env/comparison.py',ROOT/'benchmarks/comparison_suite.py'])
    if case.provider=='pandapower_balanced':
        import pandapower as pp
        from pandapower.converter.pypower.from_ppc import from_ppc
        if pp.__version__!=case.reference_version:raise ValueError('pinned pandapower required')
        c=case_from_document(case.input['case']);start=time.perf_counter();actual=solve_power_flow(c,enforce_q_limits=False)
        primary_wall=time.perf_counter()-start
        bus=np.array([[b.id,b.type_code,b.pd,b.qd,b.gs*c.base_mva,b.bs*c.base_mva,1,b.vm,b.va_deg,10,1,b.vmax,b.vmin] for b in c.buses],float)
        gen=np.zeros((len(c.generators),21))
        for i,g in enumerate(c.generators):gen[i,:10]=[g.bus,g.pg or 0,g.qg or 0,g.qmax,g.qmin,next(b.vm for b in c.buses if b.id==g.bus),c.base_mva,1,g.pmax,g.pmin]
        branch=np.array([[b.fbus,b.tbus,b.r,b.x,b.b,b.rate_a,0,0,b.ratio,b.angle_deg,b.status,b.angmin_deg,b.angmax_deg] for b in c.branches],float)
        start=time.perf_counter();net=from_ppc(dict(version='2',baseMVA=c.base_mva,bus=bus,gen=gen,branch=branch),f_hz=50)
        pp.runpp(net,algorithm='nr',calculate_voltage_angles=True,init='flat',tolerance_mva=1e-10,enforce_q_lims=False,numba=False,trafo_model='pi')
        reference_wall=time.perf_counter()-start
        if not actual['completed'] or not net.converged:raise ValueError('both power flows must converge')
        axis=[float(b.id) for b in c.buses]
        a=dict(voltage_pu=actual['vm_pu'],angle_rad=np.radians(actual['va_deg']).tolist())
        b=dict(voltage_pu=net.res_bus.vm_pu.tolist(),angle_rad=np.radians(net.res_bus.va_degree).tolist())
        raw_a=actual;raw_b=dict(converged=bool(net.converged),bus_indices=net.bus.index.tolist(),channels=b)
        ref_sources=source_files([Path(pp.__file__).parent/name for name in ['powerflow.py','build_bus.py','build_branch.py','pypower/newtonpf.py','pypower/makeYbus.py','converter/pypower/from_ppc.py']])
        reference_axis=axis
    elif case.provider=='andes_motor3':
        from benchmarks.andes_motor_reference import _native,scope
        s=StudySpec.model_validate(case.input['study']);scope(s)
        start=time.perf_counter();raw_a=run_study(s);primary_wall=time.perf_counter()-start
        start=time.perf_counter();raw_b=_native(s,case.input['native_step_s']);reference_wall=time.perf_counter()-start
        if not raw_a.get('completed') or not raw_b.get('completed'):raise ValueError('both motor trajectories must complete')
        if raw_b['backend_version']!=case.reference_version:raise ValueError('pinned ANDES required')
        axis=raw_a['time_s'];reference_axis=raw_b['time_s'];motor=s.induction.motors[0].id
        a={c.name:raw_a['channels'][motor+'.'+c.name] for c in case.channels};b=raw_b['channels'];ref_sources=raw_b['native_source_sha256']
    elif case.provider=='motulator_network':
        from power_grid_env.model import Network
        from power_grid_env.flux_network import FluxNetworkSettings,run_flux_network
        from benchmarks.flux_network_native import reference
        from benchmarks.extended_equipment_validation import verify_native
        ref_sources=verify_native(Path(wheel))
        n=Network.model_validate(case.input['network']);s=FluxNetworkSettings.model_validate(case.input['settings'])
        if any(len(m.cages)!=1 or m.core_loss_s for m in s.motors):raise ValueError('original single-cage motulator profile required')
        start=time.perf_counter();raw_a=run_flux_network(n,s);primary_wall=time.perf_counter()-start
        start=time.perf_counter();raw_b=reference(n,s);reference_wall=time.perf_counter()-start
        if not raw_a.get('completed') or not raw_b.get('completed') or not raw_b['original_motulator_rhs_and_ac_port']:raise ValueError('original completed native trajectories required')
        axis=reference_axis=raw_b['time_s'];index={t:i for i,t in enumerate(raw_a['time_s'])}
        a={c.name:[raw_a['channels'][c.name][index[t]] for t in axis] for c in case.channels};b=raw_b['channels']
    else:raise ValueError('no executable provider for this profile')
    if package_fingerprint()!=code:raise ValueError('producer changed')
    for name in ['comparison_suite.py','andes_motor_reference.py','flux_network_native.py','extended_equipment_validation.py','motulator_motor_validation.py']:
        ref_sources['adapter:benchmarks/'+name]=digest((ROOT/'benchmarks'/name).read_bytes())
    for name,data in [('raw-primary.json',raw_a),('raw-reference.json',raw_b)]: (out/name).write_bytes(encoded(data))
    for role,channels,times,backend,version,sources,wall in [
        ('primary',a,axis,'power-grid-env','0.1.0',primary_sources,primary_wall),
        ('reference',b,reference_axis,case.reference_backend,case.reference_version,ref_sources,reference_wall)]:
        trace=ComparisonTrace(completed=True,case_input_sha256=identity,producer_sha256=code,backend=backend,backend_version=version,
                              source_sha256=sources,axis=times,channels=channels,wall_s=wall)
        (out/(role+'.json')).write_bytes(encoded(trace.model_dump(mode='json')))


def run_suite(out,*,reference_dir=None,wheel=None,timeout_s=180,total_timeout_s=900,document=None):
    out=Path(out)
    if out.exists():raise ValueError('new comparison directory required')
    if not all(math.isfinite(x) and 0<x<=3600 for x in [timeout_s,total_timeout_s]):raise ValueError('bounded positive time budgets required')
    plan=registered_plan()
    if document is not None and ComparisonPlan.model_validate(document).model_dump(mode='json')!=plan.model_dump(mode='json'):
        raise ValueError('input differs from registered profile; define a separately named/qualified profile instead')
    out.mkdir(parents=True);code=package_fingerprint();sources=benchmark_sources();source=digest(encoded(sources));started=time.monotonic()
    (out/'input.json').write_bytes(encoded(plan.model_dump(mode='json')))
    (out/'benchmark-sources.json').write_bytes(encoded(sources))
    (out/'environment.json').write_bytes(encoded(dict(python=sys.version,producer_sha256=code,suite_source_sha256=source,
        comparison_purpose='numerical validation only; no performance ranking',dependencies={name:importlib.metadata.version(name) for name in ['numpy','scipy','pydantic']})))
    for case in plan.cases:
        if case.coverage!='required':continue
        path=out/'cases'/case.id;path.mkdir(parents=True);(path/'case.json').write_bytes(encoded(case.model_dump(mode='json')))
        remaining=total_timeout_s-(time.monotonic()-started);reason=None
        if remaining<=0:reason='total_timeout'
        else:
            command=[sys.executable,'-m','benchmarks.comparison_suite','--worker',str((path/'case.json').resolve()),'--out',str(path.resolve())]
            if reference_dir:command+=['--reference-dir',str(Path(reference_dir).resolve())]
            if wheel:command+=['--wheel',str(Path(wheel).resolve())]
            env=os.environ.copy();env['PYTHONPATH']=str(ROOT);env['PYTHONDONTWRITEBYTECODE']='1'
            start=time.monotonic()
            with (path/'worker.log').open('wb') as log:
                p=subprocess.Popen(command,env=env,cwd=path,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
                try:
                    while p.poll() is None:
                        if time.monotonic()-start>=min(timeout_s,remaining):reason='timeout'
                        elif (path/'worker.log').stat().st_size>1000000:reason='log_budget'
                        elif any(f.stat().st_size>64000000 for f in path.glob('*.json')):reason='output_budget'
                        if reason:kill_process_group(p);break
                        time.sleep(.02)
                    if not reason and p.returncode!=0:reason='provider_failed'
                finally:
                    if p.poll() is None:kill_process_group(p)
        if package_fingerprint()!=code or benchmark_sources()!=sources:raise ValueError('comparison implementation changed')
        (path/'outcome.json').write_bytes(encoded(dict(status='failed',reason=reason) if reason else dict(status='completed')))
        inventory(out)
    if time.monotonic()-started>=total_timeout_s:
        # Even a late successful final worker must not receive qualification.
        last=next(c for c in reversed(plan.cases) if c.coverage=='required')
        (out/'cases'/last.id/'outcome.json').write_bytes(encoded(dict(status='failed',reason='total_timeout')))
    return finalize_comparison(out)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True);parser.add_argument('--input');parser.add_argument('--worker');parser.add_argument('--reference-dir');parser.add_argument('--wheel')
    args=parser.parse_args()
    if args.reference_dir:sys.path.insert(0,str(Path(args.reference_dir).resolve()))
    if args.worker:run_provider(read_json(args.worker),args.out,args.wheel)
    else:
        r=run_suite(args.out,reference_dir=args.reference_dir,wheel=args.wheel,document=read_json(args.input) if args.input else None);print(encoded(r).decode());raise SystemExit(0 if r['required_comparisons_passed'] else 1)
