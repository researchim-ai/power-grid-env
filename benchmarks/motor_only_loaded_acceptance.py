"""Owner-bound recomputation of loaded initialization and SI winding evidence."""
from pathlib import Path
import numpy as np
from scipy.optimize import linear_sum_assignment
from power_grid_env.study_spec import StudySpec
from power_grid_env.model import canonical_hash
from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest
from power_grid_env.evidence import package_fingerprint
from power_grid_env.coupled_rms import CoupledNetwork
from benchmarks.motor_only_loaded_qualification import jobs,evaluate
from benchmarks.motor_only_loaded_reference import WindingReference
from benchmarks.running_motor_winding_reference import compare as compare_winding
from benchmarks.closed_loop_protection_reference import compare_solvers
from benchmarks.running_fault_cartesian import CartesianProtocol


def verify_state(study,report):
    model=CoupledNetwork(study.network,study.coupled);state=model.initial_state();ch=report['channels']
    for i,m in enumerate(study.coupled.motors):
        state[3*i:3*i+3]=[ch[m.id+'.'+k][0] for k in ['e_real_pu','e_imag_pu','speed_pu']]
        if i in model.thermal_indices:state[model.thermal_indices[i]]=ch[m.id+'.thermal_level'][0]
        if i in model.cages.addresses:
            state[model.cages.addresses[i]]=[ch[m.id+'.cage1_e_real_pu'][0],ch[m.id+'.cage1_e_imag_pu'][0]]
    voltage=model.electrical(state)[0]
    indices=list(range(model.electrical_size))+[i for address in model.cages.addresses.values() for i in range(address.start,address.stop)]
    residual=float(max(abs(model.physical_rhs(0.,state)[indices])))
    kcl=float(max(abs(model.matrix@voltage-model.network_injection(state))))
    if residual>1e-8 or kcl>1e-8:raise ValueError('saved loaded state does not satisfy original equations')
    return dict(original_machine_residual_per_s=residual,network_kcl_residual_pu=kcl)


def qualify(protocol,source,out):
    requested=CartesianProtocol.model_validate(protocol);source=Path(source);anchor=verify(source/'index')
    if CartesianProtocol.model_validate(read_json(source/'index/protocol.json')).model_dump(mode='json')!=requested.model_dump(mode='json'):
        raise ValueError('loaded owner protocol differs')
    stored=read_json(source/'index/summary.json')
    if not stored['passed'] or stored['code_sha256']!=package_fingerprint():raise ValueError('loaded source not qualified under this runtime')
    if stored['producer_sha256']!=digest(Path('benchmarks/motor_only_loaded_qualification.py').read_bytes()) or stored['reference_sha256']!=digest(Path('benchmarks/motor_only_loaded_reference.py').read_bytes()):
        raise ValueError('loaded benchmark/reference producer differs')
    declared=list(jobs(requested));by_id={j['id']:j for j in declared}
    if {x.name for x in source.iterdir()}!=set(by_id)|{'index','references'}:raise ValueError('loaded source study coverage differs')
    if {r['id'] for r in stored['rows']}!=set(by_id) or len(stored['rows'])!=320:raise ValueError('loaded index coverage differs')
    entries={r['id']:r for r in stored['rows']};rows=[]
    for j in declared:
        child=source/j['id'];before=verify(child)
        if before!=entries[j['id']]['manifest_sha256'] or read_json(child/'study.json')!=j['study']:raise ValueError('loaded owner study/manifest differs')
        study=StudySpec.model_validate(j['study']);report=read_json(child/'trace.json')
        result=evaluate(j,report)
        if not result['passed'] or result!=read_json(child/'result.json'):raise ValueError('loaded native outcome recomputation differs')
        if j['category']=='negative':
            if j['scenario']=='no_connected':
                if any(m.connected for m in study.coupled.motors):raise ValueError('no-connected rejection not grounded in owner input')
            elif j['scenario']=='thermal_limit':
                ref=WindingReference(study);state,_=ref.initialize()
                if state[3]<ref.m.thermal.trip_level:raise ValueError('thermal-limit rejection not reproduced independently')
            else:
                try:WindingReference(study).initialize()
                except ValueError as exc:
                    if 'no bracketed high-speed' not in str(exc):raise
                else:raise ValueError('bounded-branch rejection not independently reproduced')
            row=dict(id=j['id'],negative_reproduced=True)
        else:
            if report.get('settings')!=study.coupled.model_dump(mode='json') or report.get('network_sha256')!=canonical_hash(study.network) or report.get('code_sha256')!=package_fingerprint():raise ValueError('loaded trace identity differs')
            row=dict(id=j['id'],**verify_state(study,report))
            if j['category']=='single':
                ref=WindingReference(study);state,initial=ref.initialize();m=study.coupled.motors[0]
                if abs(initial['speed_pu']-report['channels'][m.id+'.speed_pu'][0])>1e-8 or abs(initial['current_pu']-report['channels'][m.id+'.current_pu'][0])>1e-8:
                    raise ValueError('native loaded root differs from independent T circuit')
        if verify(child)!=before:raise ValueError('loaded child changed during audit')
        rows.append(row)
    if len(stored['references'])!=48 or {x.name for x in (source/'references').iterdir()}!={r['id'] for r in stored['references']}:raise ValueError('independent reference coverage differs')
    references=[]
    for entry in stored['references']:
        j=by_id[entry['id']];child=source/'references'/j['id'];before=verify(child)
        if before!=entry['manifest_sha256']:raise ValueError('reference manifest differs')
        native=read_json(source/j['id']/'trace.json');values,initial=WindingReference(j['study']).trajectory(native['time_s'],native['sample_side'])
        saved=read_json(child/'trajectory.json')
        if values!=saved:raise ValueError('independent trajectory recomputation differs')
        for method in ['radau','network_dae']:
            other=next(k for k in declared if all(k.get(key)==j.get(key) for key in ['category','scenario','condition_id','thermal','grid']) and k['method']==method)
            report=read_json(source/other['id']/'trace.json');check=compare_winding(other['study'],report,values)
            if not check['passed'] or check!=entry['checks']['dae' if method=='network_dae' else 'radau']:raise ValueError('independent/native check differs')
            for fault in j['study']['coupled']['faults']:
                error=max(abs(v['fault_current_a'][fault['id']]-report['channels'][fault['id']+'.current_a'][i]) for i,v in enumerate(values))
                if error>1e-3:raise ValueError('independent fault-current check failed')
        native_modes=np.array([complex(*z) for z in native['operating_point']['connected_motor_eigenvalues_per_s']]);expected_modes=np.array([complex(*z) for z in initial['eigenvalues_per_s']])
        ii,jj=linear_sum_assignment(abs(native_modes[:,None]-expected_modes[None,:]));eigen_error=float(max(abs(native_modes[ii]-expected_modes[jj])))
        if eigen_error>1e-4 or verify(child)!=before:raise ValueError('independent stability audit failed/changed')
        references.append(dict(id=j['id'],eigenvalue_error_per_s=eigen_error,passed=True))
        print('verified loaded winding',len(references),'/48',flush=True)
    for pair in stored['solver_pairs']:
        j=by_id[pair['id']]
        other=next(k for k in declared if all(k.get(key)==j.get(key) for key in ['category','scenario','condition_id','thermal','grid']) and k['method']=='radau')
        expected=compare_solvers(read_json(source/other['id']/'trace.json'),read_json(source/j['id']/'trace.json'))
        if not expected['passed'] or dict(id=j['id'],**expected)!=pair:raise ValueError('loaded solver-pair recomputation differs')
    for entry in stored['rows']:
        if verify(source/entry['id'])!=entry['manifest_sha256']:raise ValueError('previous loaded study changed')
    if verify(source/'index')!=anchor:raise ValueError('loaded source index changed')
    result=dict(passed=True,code_sha256=package_fingerprint(),source_index_sha256=anchor,native_runs=320,
        accepted_single_runs=288,accepted_group_runs=24,negative_initializations=8,solver_pairs=156,
        independent_loaded_trajectories=48,independent_native_comparisons=96,independent_programmed_fault_trajectories=16,
        rows=rows,references=references,normative_approval=False,full_DES04_complete=False,
        active_protection_independently_qualified=False,scope='Owner inputs, original initialized equations, independent T-circuit roots,48 SI winding trajectories, eigenmodes and156 solver pairs recomputed. Programmed faults included, active network/UV/thermal protection excluded from independent reference.')
    root=Path(out)
    if root.resolve().is_relative_to(source.resolve()):raise ValueError('acceptance output must be outside loaded source')
    root.mkdir(parents=True,exist_ok=False);(root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--protocol',required=True);p.add_argument('--source',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=qualify(read_json(a.protocol),a.source,a.out);print(dict(passed=r['passed'],native_runs=320))
