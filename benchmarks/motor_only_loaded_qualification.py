"""Loaded motor-only native qualification and independent SI root/trajectory."""
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
import copy

import numpy as np
from scipy.optimize import linear_sum_assignment
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest
from power_grid_env.evidence import package_fingerprint
from benchmarks.running_fault_cartesian import CartesianProtocol,conditions,condition_jobs
from benchmarks.running_motor_winding_reference import compare as compare_winding
from benchmarks.closed_loop_protection_reference import compare_solvers
from benchmarks.motor_only_loaded_reference import WindingReference


def jobs(protocol):
    p=CartesianProtocol.model_validate(protocol)
    for condition_id,condition in conditions(p):
        template=next(j for j in condition_jobs(p,p.envelope.base,condition) if j['kind']=='normal' and j['scenario']=='start' and j['method']=='radau' and j['grid']==0)['study']
        for thermal in ['passport','equilibrium']:
            for mode in ['hold','repeated_sag','torque_step','coast_restart','plant_fault','grid_fault']:
                for method in ['radau','network_dae']:
                    for grid in range(3):
                        s=copy.deepcopy(template);c=s['coupled'];m=c['motors'][0]
                        c.update(duration_s=.6,step_s=.002/2**grid,integration_method=method,
                                 operating_point=dict(thermal_state=thermal),events=[],faults=[])
                        m['contactor']=None
                        for r in c['network_automation']['relays']:r['pickup']=1e8
                        if mode=='repeated_sag':
                            c['events']=[dict(time_s=t,kind='source_voltage',equipment_id=s['network']['sources'][0]['id'],value=v) for t,v in [(.1,.5),(.15,1),(.3,.65),(.35,1)]]
                        elif mode=='torque_step':c['events']=[dict(time_s=.2,kind='load_torque_scale',equipment_id=m['id'],value=1.25)]
                        elif mode=='coast_restart':c['events']=[dict(time_s=t,kind=k,equipment_id=m['id']) for t,k in [(.2,'motor_open'),(.3,'motor_close')]]
                        elif mode.endswith('_fault'):
                            source=s['network']['sources'][0]
                            node=m['node_id'] if mode=='plant_fault' else next(t['connectivity_node_id'] for t in s['network']['terminals'] if t['id']==source['terminal_id'])
                            c['faults']=[dict(id='loaded-fault',node_id=node,apply_s=.1,clear_s=.2,r_ohm=.001,x_ohm=.0005)]
                        name=f'{condition_id}-{thermal}-{mode}-{method}-grid{grid}'
                        yield dict(id=name,category='single',condition_id=condition_id,thermal=thermal,scenario=mode,method=method,grid=grid,study=StudySpec.model_validate(s).model_dump(mode='json'))
    # Symmetric groups qualify state construction/cage allocation and stationary
    # trajectories; the independent winding oracle above is single-cage only.
    template=next(j for j in jobs_single_template(p))
    for count in [2,4]:
        for cage in ['single_cage','double_cage']:
            for method in ['radau','network_dae']:
                for grid in range(3):
                    s=copy.deepcopy(template);c=s['coupled'];first=c['motors'][0];first['rated_va']/=count
                    c['motors']=[copy.deepcopy(first) for _ in range(count)]
                    c.update(duration_s=.2,step_s=.002/2**grid,integration_method=method,operating_point=dict(thermal_state='equilibrium'),events=[],faults=[])
                    for i,m in enumerate(c['motors']):
                        m['id']='group'+str(i);m['contactor']=None
                        if cage=='double_cage':m.update(model='double_cage',rotor2_r_pu=.06,rotor2_x_pu=.2)
                    for r in c['network_automation']['relays']:r['pickup']=1e8
                    yield dict(id=f'group{count}-{cage}-{method}-grid{grid}',category='group',scenario=f'group{count}-{cage}',method=method,grid=grid,study=StudySpec.model_validate(s).model_dump(mode='json'))
    for defect in ['no_connected','overloaded','speed_bound','thermal_limit']:
        for method in ['radau','network_dae']:
            s=copy.deepcopy(template);c=s['coupled'];m=c['motors'][0]
            c.update(duration_s=.2,integration_method=method,operating_point=dict(thermal_state='equilibrium'),events=[],faults=[])
            m['contactor']=None
            if defect=='no_connected':m['connected']=False
            elif defect=='overloaded':m['fan_torque_pu']=1000
            elif defect=='speed_bound':c['operating_point']['minimum_speed_pu']=.999
            else:m['thermal']['trip_level']=.01
            expected='no_connected_motor_for_operating_point' if defect=='no_connected' else 'initial_thermal_limit' if defect=='thermal_limit' else 'no_qualified_coupled_operating_point'
            yield dict(id=f'reject-{defect}-{method}',category='negative',scenario=defect,method=method,expected_reason=expected,study=StudySpec.model_validate(s).model_dump(mode='json'))


def jobs_single_template(p):
    _,condition=next(conditions(p))
    yield next(j['study'] for j in condition_jobs(p,p.envelope.base,condition) if j['kind']=='normal' and j['scenario']=='start' and j['method']=='radau' and j['grid']==0)


def evaluate(job,report):
    if job['category']=='negative':
        return dict(passed=not report.get('completed') and report.get('status')=='initialization_failed' and report.get('reason')==job['expected_reason'],profile_accepted=False)
    if not report.get('completed') or not report.get('solved'):return dict(passed=False,reason='native_incomplete')
    op=report['operating_point'];checks=dict(mode=op['mode']=='motor_only_loaded_equilibrium',
        network=op['network_kcl_residual_pu']<1e-8,voltage_consistency=op['static_dynamic_voltage_mismatch_pu']<1e-8,
        original_rhs=op['checked_dynamic_residual_per_s']<1e-8,
        stable=op['connected_machine_spectral_abscissa_per_s']<-1e-7,
        no_fake_generator=op['generator_constraints']==0 and report['generator_mapping']=={},
        inactive_automation=op['network_automation_stationary_initialization'])
    if job['scenario']=='hold' or job['category']=='group':
        for m in job['study']['coupled']['motors']:
            for key in ['speed_pu','e_real_pu','e_imag_pu']:
                values=np.array(report['channels'][m['id']+'.'+key]);checks[m['id']+'.'+key]=float(max(abs(values-values[0])))<1e-7
            if job.get('thermal','equilibrium')=='equilibrium':
                values=np.array(report['channels'][m['id']+'.thermal_level']);checks[m['id']+'.heat']=float(max(abs(values-values[0])))<1e-7
    return dict(passed=all(checks.values()),checks=checks)


def execute(args):
    job,path=args;root=Path(path);report=run_study(StudySpec.model_validate(job['study']));result=evaluate(job,report)
    root.mkdir(parents=True,exist_ok=False)
    for name,value in [('study',job['study']),('trace',report),('result',result)]:
        (root/(name+'.json')).write_bytes(encoded(value))
    seal(root);return dict(**{k:v for k,v in job.items() if k!='study'},passed=result['passed'],manifest_sha256=verify(root))


def qualify(protocol,out,workers=8):
    if not 1<=workers<=16:raise ValueError('workers must be1..16')
    source=package_fingerprint();root=Path(out);root.mkdir(parents=True,exist_ok=False)
    (root/'index').mkdir()
    declared=list(jobs(protocol));rows=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(execute,(j,str(root/j['id']))) for j in declared]):
            rows.append(future.result())
            if len(rows)%24==0:print('loaded native',len(rows),'/320',flush=True)
    pairs=[];references=[]
    for job in declared:
        if job['category']=='negative' or job['method']!='network_dae':continue
        other=next(j for j in declared if j['category']==job['category'] and j['scenario']==job['scenario'] and j.get('condition_id')==job.get('condition_id') and j.get('thermal')==job.get('thermal') and j.get('grid')==job.get('grid') and j['method']=='radau')
        a=read_json(root/other['id']/'trace.json');b=read_json(root/job['id']/'trace.json')
        pairs.append(dict(id=job['id'],**compare_solvers(a,b)))
        if job['category']!='single' or job['grid']!=2:continue
        ref=WindingReference(job['study']);values,initial=ref.trajectory(b['time_s'],b['sample_side'])
        checked=dict(radau=compare_winding(other['study'],a,values),dae=compare_winding(job['study'],b,values))
        faults={}
        for method,report in [('radau',a),('dae',b)]:
            for fault in job['study']['coupled']['faults']:
                error=max(abs(row['fault_current_a'][fault['id']]-report['channels'][fault['id']+'.current_a'][i]) for i,row in enumerate(values))
                faults[method+':'+fault['id']]=dict(passed=error<=1e-3,error_a=error,limit_a=1e-3)
        native_modes=np.array([complex(*z) for z in b['operating_point']['connected_motor_eigenvalues_per_s']]);ref_modes=np.array([complex(*z) for z in initial['eigenvalues_per_s']])
        ii,jj=linear_sum_assignment(abs(native_modes[:,None]-ref_modes[None,:]));mode_error=float(max(abs(native_modes[ii]-ref_modes[jj])))
        passed=all(c['passed'] for c in checked.values()) and all(c['passed'] for c in faults.values()) and mode_error<1e-4
        entry=dict(id=job['id'],passed=passed,checks=checked,faults=faults,reference_initial=initial,eigenvalue_error_per_s=mode_error)
        child=root/'references'/job['id'];child.mkdir(parents=True)
        (child/'trajectory.json').write_bytes(encoded(values));(child/'checks.json').write_bytes(encoded(entry));seal(child)
        entry['manifest_sha256']=verify(child)
        references.append(entry);print('loaded winding',len(references),'/48',passed,flush=True)
    if package_fingerprint()!=source:raise ValueError('runtime changed during loaded qualification')
    result=dict(passed=len(rows)==320 and all(r['passed'] for r in rows) and all(p['passed'] for p in pairs) and len(references)==48 and all(r['passed'] for r in references),
        native_runs=320,single_positive_runs=288,group_positive_runs=24,initialization_negative_runs=8,
        independent_loaded_trajectories=48,independent_native_comparisons=96,solver_pairs=pairs,rows=sorted(rows,key=lambda r:r['id']),references=references,
        code_sha256=source,producer_sha256=digest(Path(__file__).read_bytes()),reference_sha256=digest(Path('benchmarks/motor_only_loaded_reference.py').read_bytes()),
        normative_approval=False,scope='Loaded motor-only physical equilibrium, two thermal policies;48 independent single-cage normal/programmed-fault trajectories and native1/2/4-motor single/double-cage hold checks. Network OC intentionally inactive; active protection trajectories not independently qualified.')
    (root/'index/protocol.json').write_bytes(encoded(protocol));(root/'index/summary.json').write_bytes(encoded(result));seal(root/'index')
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--protocol',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=8)
    a=p.parse_args();r=qualify(read_json(a.protocol),a.out,a.workers);print(dict(passed=r['passed'],native_runs=320))
    if not r['passed']:raise SystemExit(2)
