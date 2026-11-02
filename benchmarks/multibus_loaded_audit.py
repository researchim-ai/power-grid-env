"""Independent port/current and initial-state audit of the loaded corpus."""
from pathlib import Path
import argparse,json,math
import numpy as np
from benchmarks.multibus_loaded_validation import load_case
from benchmarks.multibus_operating_point import validate_scope
from benchmarks.multibus_motor_audit import check_files
from power_grid_env.report import write_json,sha256_file
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint

ROOT=Path(__file__).resolve().parents[1]


def current_closure(spec,report):
    circuit=validate_scope(spec);n=len(report['time_s']);v=np.empty((circuit.N,n),complex)
    for node,i in circuit.nodes.items():
        data=report['node_voltage'][node];v[i]=np.array(data['real_v'])+1j*np.array(data['imag_v'])
    if not np.isfinite(v).all() or np.any(abs(v)<1e-12):raise ValueError('current closure requires finite nonzero port voltages')
    mismatch=circuit.Y@v;settings=spec.coupled if spec.coupled else spec.induction
    for motor in settings.motors:
        bus=circuit.nodes[motor.node_id];power=np.array(report['channels'][motor.id+'.p_w'])+1j*np.array(report['channels'][motor.id+'.q_var'])
        mismatch[bus]+=np.conj(power/(3*v[bus]))
    terminals={t.id:t.connectivity_node_id for t in spec.network.terminals}
    for generator in spec.network.generators:
        bus=circuit.nodes[terminals[generator.terminal_id]];power=np.array(report['channels'][generator.id+'.p_w'])+1j*np.array(report['channels'][generator.id+'.q_var'])
        mismatch[bus]-=np.conj(power/(3*v[bus]))
    for j,(at,side) in enumerate(zip(report['time_s'],report['sample_side'])):
        scale=1.
        for event in sorted(settings.events,key=lambda e:e.time_s):
            if event.kind=='source_voltage' and (event.time_s<at or (event.time_s==at and side!='pre_event')):scale=event.value
        mismatch[circuit.source,j]-=circuit.es*scale/circuit.zs
    bases=spec.network.s_base_va/(3*circuit.base)
    result=float(np.max(abs(mismatch)/bases[:,None]))
    if not math.isfinite(result):raise ValueError('nonfinite physical KCL')
    return result


def audit(root):
    root=Path(root);read=lambda p:json.loads((root/p).read_text());summary=read('summary.json');protocol=read('protocol.json')
    if not summary['passed'] or not summary['full_registered_corpus'] or len(summary['cases'])!=9 or len(summary['rejected_cases'])!=3 or not all(r['passed'] for r in summary['rejected_cases']):raise ValueError('complete passing loaded corpus required')
    check_files(root,summary['artifacts_sha256']);check_files(ROOT,protocol['source_sha256'])
    if package_fingerprint()!=protocol['code_sha256']:raise ValueError('loaded core changed')
    rows=[];native_sources=[];steps=0;rollbacks=0;max_rhs=0.;max_speed_error=0.;max_q_error=0.
    for name in protocol['cases']:
        spec=load_case(name);settings=spec.coupled if spec.coupled else spec.induction
        if canonical_hash(spec)!=protocol['case_sha256'][name]:raise ValueError('loaded case changed')
        independent=read(name+'/SI.json');primary=read(name+'/production.json')
        for h in protocol['native_steps_s']:
            native=read(name+f'/andes-{h}.json')
            if not native['completed'] or native['case_sha256']!=canonical_hash(spec) or native['code_sha256']!=protocol['code_sha256'] or native['step_s']!=h or native['backend_version']!='2.0.0':raise ValueError('loaded native identity mismatch')
            if native['controller_commands_precomputed'] or not native['stock_generated_cache_unchanged']:raise ValueError('loaded native provenance contract failed')
            init=native['initialization'];rhs=init['maximum_generator_initial_rhs']
            if settings.operating_point:rhs=max(rhs,init['maximum_motor_initial_rhs'])
            if not math.isfinite(rhs) or rhs>protocol['initial_motor_and_generator_rhs_limit']:raise ValueError('loaded initial RHS exceeded')
            max_rhs=max(max_rhs,rhs)
            for motor in settings.motors:
                values=[r['channels'][motor.id+'.speed_pu'][0] for r in [primary,independent,native]]
                error=float(max(values)-min(values));max_speed_error=max(max_speed_error,error)
                if error>protocol['initial_motor_speed_agreement_limit_pu'] or (settings.operating_point and min(values)<settings.operating_point.minimum_speed_pu) or (not settings.operating_point and any(v!=0 for v in values)):raise ValueError('loaded motor initial state mismatch')
            if spec.coupled:
                error=abs(init['generator_initial_q_var']-independent['initialization']['generator_initial_q_var']);max_q_error=max(max_q_error,error)
                if error>protocol['initial_generator_Q_agreement_limit_var']:raise ValueError('independent initial PV Q mismatch')
            closure=current_closure(spec,native)
            if closure>protocol['physical_current_KCL_limit_pu']:raise ValueError('loaded physical KCL exceeded')
            native_sources.append(native['native_source_sha256']);steps+=native['native_steps_executed'];rollbacks+=native['native_driver_metadata']['rollback_count']
            rows.append(dict(case_id=name,step_s=h,maximum_physical_KCL_pu=closure))
    if len(native_sources)!=27 or any(s!=native_sources[0] for s in native_sources):raise ValueError('native backend changed across loaded runs')
    result=dict(passed=True,native_runs=27,negative_initialization_cases=3,accepted_native_steps=steps,root_rollbacks=rollbacks,
        maximum_physical_KCL_pu=max(r['maximum_physical_KCL_pu'] for r in rows),maximum_initial_rhs=max_rhs,maximum_initial_motor_speed_disagreement_pu=max_speed_error,maximum_initial_generator_Q_disagreement_var=max_q_error,
        identical_native_backend_sources=True,rows=rows,protocol_sha256=sha256_file(root/'protocol.json'),summary_sha256=sha256_file(root/'summary.json'),field_validated=False,industrial_profile_qualified=False)
    write_json(root/'audit.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);args=p.parse_args();audit(args.root)
