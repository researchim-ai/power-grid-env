"""PHY-21 measured dynamic matrix: explicit separate coverage tracks.

Fresh processes, unchanged physical limits, all failures retained. Resources
include the whole process high-water mark, not incremental solver allocation.
"""
import argparse
import copy
import hashlib
import math
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time
import numpy as np
from scipy.linalg import expm
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify,digest

SCALE_NAMES=('mesh_64_4_1','mesh_256_8_2','mesh_512_64_16','mesh_2000_128_32')
ISLAND_NAMES=('islands_matched','islands_mismatch')
AUTO_NAMES=('automation_breaker_failure','automation_standby_transfer','automation_failed_primary_transfer')
PHASE_NAMES=('phase_12','phase_48','phase_96')
NAMES=SCALE_NAMES+ISLAND_NAMES+AUTO_NAMES+PHASE_NAMES
REPEATS=2
MODES=('radau','network_dae')


def identity():
    return dict(package=package_fingerprint(),benchmarks={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')})


def study(name,mode='network_dae',reverse=False):
    if name in SCALE_NAMES:
        count=tuple(map(int,name.split('_')[1:]))
        if count[0]>=512:
            from benchmarks.dae_cases import scale_case
            s=scale_case(*count,duration_s=.6,voltage_profile='nominal_1p0')
        else:
            from benchmarks.ex03_cases import scale_case
            s=scale_case(*count);s.coupled.duration_s=.6
            for g in s.network.generators:g.v_set_pu=1.
        from power_grid_env.coupled_rms import CoupledEvent
        s.coupled.step_s=.05
        s.coupled.events=[CoupledEvent(time_s=.05,kind='motor_close',equipment_id=m.id) for m in s.coupled.motors]
        s.coupled.events += [CoupledEvent(time_s=t,kind='source_voltage',equipment_id=s.network.sources[0].id,value=v) for t,v in [(.2,.7),(.3,1.),(.42,.8),(.48,1.)]]
        s.id='phy21-'+name
    elif name in ISLAND_NAMES:
        from benchmarks.long_islands import case
        s=case(mode,name.removeprefix('islands_'))
    elif name in AUTO_NAMES:
        from benchmarks.network_automation_reference import example
        s=example(name.removeprefix('automation_'))
    else:raise ValueError('registered RMS dynamic case required')
    p=s.coupled;p.integration_method=mode;p.jacobian_network='direct_solve';p.jacobian_format='csc';p.event_policy='coincident_roots_v1'
    if reverse:
        p.machines.reverse();p.motors.reverse();p.events.reverse()
        s.network.lines.reverse();s.network.generators.reverse();s.network.connectivity_nodes.reverse();s.network.terminals.reverse()
        if p.network_automation:
            p.network_automation.breakers.reverse();p.network_automation.relays.reverse();p.network_automation.transfers.reverse()
    return StudySpec.model_validate(s.model_dump(mode='json'))


def phase_document(name):
    if name not in PHASE_NAMES:raise ValueError('registered phase size required')
    count=int(name.split('_')[1]);nodes=['sa','sb','sc'];resistors=[];capacitors=[];inductors=[];diagonal=[];events=[]
    for k in range(count):
        node='n'+str(k);nodes.append(node)
        resistors.append(dict(id='r'+str(k),frm=node,resistance_ohm=20.+k%3*5))
        capacitors.append(dict(id='c'+str(k),frm=node,capacitance_f=(80.+k%3*20)*1e-6))
        inductors.append(dict(id='l'+str(k),frm='s'+'abc'[k%3],to=node,resistance_ohm=1.+k%3*.2))
        diagonal.append(.02+k%3*.005)
    for t,factor in [(.006,.5),(.012,1.),(.016,.75)]:
        events.extend(dict(time_s=t,kind='resistance',equipment_id='r'+str(k),value=(20.+k%3*5)*factor) for k in range(count))
    return dict(id='phy21-'+name,nodes=nodes,sources=[dict(id='grid'+p,node='s'+p,tones=[dict(order=1,rms=r,phase_rad=a)]) for p,r,a in zip('abc',[230.,190.,220.],[0.,-2*math.pi/3,2*math.pi/3])],resistors=resistors,capacitors=capacitors,inductors=inductors,inductance_h=np.diag(diagonal).tolist(),events=events,provenance=dict(all='Authored unbalanced independent RLC branches under three stiff phase sources; exact matrix-exponential oracle; not a general meshed phase network'))


def phase_reference(name):
    p=phase_document(name);count=len(p['inductors']);times=[];sides=[];channels={};state=np.array([[0.,0.,math.cos([0.,-2*math.pi/3,2*math.pi/3][k%3]),math.sin([0.,-2*math.pi/3,2*math.pi/3][k%3])] for k in range(count)])
    def record(t,side):
        times.append(float(t));sides.append(side)
        for k in range(count):
            channels.setdefault('n'+str(k)+'.voltage_v',[]).append(float(state[k,1]))
            channels.setdefault('l'+str(k)+'.current_a',[]).append(float(state[k,0]))
    record(0.,'initial');previous=0.;factor=1.
    for t in np.arange(1,21)*.001:
        for k in range(count):
            group=k%3;r=1.+group*.2;l=.02+group*.005;c=(80.+group*20)*1e-6;load=(20.+group*5)*factor
            amplitude=math.sqrt(2)*[230.,190.,220.][group];omega=2*math.pi*50
            a=np.array([[-r/l,-1/l,amplitude/l,0.],[1/c,-1/(c*load),0.,0.],[0.,0.,0.,-omega],[0.,0.,omega,0.]])
            state[k]=expm(a*(t-previous))@state[k]
        previous=t;event=next((f for at,f in [(.006,.5),(.012,1.),(.016,.75)] if abs(t-at)<1e-12),None)
        record(t,'pre_event' if event is not None else 'regular')
        if event is not None:factor=event;record(t,'post_event')
    return dict(completed=True,time_s=times,sample_side=sides,channels=channels,independent_equations=True,production_stamps_used=False,scope='exact independent 4x4 branch exponentials; two electrical and two sinusoidal forcing states per branch')


def peak_rss():
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('VmHWM:'):return int(line.split()[1])
    raise ValueError('current-exec Linux VmHWM unavailable')


def worker(name,role,out,mode,anchor=None,reverse=False):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);before=identity();affinity=sorted(os.sched_getaffinity(0));os.sched_setaffinity(0,[affinity[0]])
    started=time.perf_counter();cpu=time.process_time()
    if name in PHASE_NAMES:
        if role=='reference':result=phase_reference(name)
        else:
            from power_grid_env.phase_network import PhaseCase,run_emt
            result=run_emt(PhaseCase.model_validate(phase_document(name)),duration_s=.02,sample_s=.001,max_step_s=.0001,rtol=1e-9,atol=1e-11,method='Radau')
        spec=phase_document(name);states=2*int(name.split('_')[1])
    else:
        s=study(name,mode,reverse);spec=s.model_dump(mode='json')
        if role=='primary':
            from unittest.mock import patch
            from power_grid_env.coupled_rms import CoupledNetwork
            original=CoupledNetwork.__init__;dimensions=[]
            def construct(model,*args,**kwargs):
                original(model,*args,**kwargs);dimensions.append(model.dynamic_end)
            with patch.object(CoupledNetwork,'__init__',construct):result=run_study(s)
            if len(dimensions)!=1:raise ValueError('exactly one coupled solver model required')
            states=dimensions[0]
        elif name in SCALE_NAMES:
            from benchmarks.ex03_reference import reference
            result=reference(s,maximum_step_s=.005).run(read_json(anchor));result.update(completed=True)
        elif name in ISLAND_NAMES:
            from benchmarks.long_islands import reference
            result=reference(name.removeprefix('islands_'))
        else:
            from benchmarks.network_automation_reference import reference,example
            key=name.removeprefix('automation_');result=reference(key,example(key))
        if role!='primary':states=None
    result['code_sha256']=before['package']
    elapsed=time.perf_counter()-started;cpu_time=time.process_time()-cpu
    (out/'study.json').write_bytes(encoded(spec));(out/'trace.json').write_bytes(encoded(result))
    metrics=dict(completed=result.get('completed',False),case=name,role=role,mode=mode,reversed_equipment=reverse,wall_s=elapsed,cpu_s=cpu_time,current_exec_peak_rss_kib=peak_rss(),process_lifetime_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,cpu_affinity=sorted(os.sched_getaffinity(0)),states=states,study_sha256=digest(encoded(spec)),producer=before,memory_scope='current-exec VmHWM including imports, construction, solve and serialization; not incremental solver allocation',host_load_controlled=False)
    (out/'metrics.json').write_bytes(encoded(metrics))
    if identity()!=before:raise RuntimeError('producer changed during worker execution')


def compare(name,spec,primary,reference):
    if not primary.get('completed') or not reference.get('completed'):raise ValueError('completed trajectories required')
    if name in SCALE_NAMES:
        from benchmarks.ex03_validation import compare as evaluate
        return evaluate(spec,primary,reference)
    if name in ISLAND_NAMES:
        from benchmarks.long_islands import compare as evaluate
        check=evaluate(primary,reference)
        def islands(trace):
            counts=[]
            for i in range(len(trace['time_s'])):
                groups=[{0},{1},{2}]
                for key,a,b in [('feed_a',0,1),('feed_b',0,2),('tie',1,2)]:
                    flag=trace['channels'][key+'.closed'][i]
                    if flag not in (0.,1.):raise ValueError('nonbinary island edge')
                    if flag:
                        left=next(g for g in groups if a in g);right=next(g for g in groups if b in g)
                        if left is not right:left.update(right);groups.remove(right)
                counts.append(len(groups))
            return counts
        counts=islands(primary);wanted=islands(reference)
        check['maximum_actual_islands']=max(counts)
        check['island_formation_confirmed']=max(counts)==max(wanted)==3
        check['passed']=bool(check['passed'] and check['island_formation_confirmed'])
    elif name in AUTO_NAMES:
        from benchmarks.network_automation_reference import compare as evaluate
        check=evaluate(primary,reference)
        if name.endswith('transfer'):
            check['break_before_make']=not any(a and b for a,b in zip(primary['channels']['grid-cb.closed'],primary['channels']['reserve-cb.closed']))
            check['passed']=bool(check['passed'] and check['break_before_make'])
    else:
        if not reference.get('independent_equations') or reference.get('production_stamps_used') is not False:raise ValueError('independent phase reference required')
        if primary['sample_side']!=reference['sample_side'] or not np.allclose(primary['time_s'],reference['time_s'],rtol=0,atol=1e-12):raise ValueError('phase one-sided clocks differ')
        errors={}
        for key,value in reference['channels'].items():
            a=np.asarray(primary['channels'][key]);b=np.asarray(value)
            if a.shape!=b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('invalid phase channel')
            errors[key]=float(max(abs(a-b)))
        ledger=primary['energy'][-1];energy=abs(ledger['residual_j'])/max(1.,abs(ledger['entered_j']),abs(ledger['loss_j']))
        check=dict(passed=max(errors.values())<=2e-4 and energy<=1e-5 and primary['kcl_max_a']<=1e-7,errors=errors,absolute_channel_cap=2e-4,energy_relative_error=energy,kcl_max_a=primary['kcl_max_a'])
        return check
    checks=[primary['maximum_kcl_residual_pu'],primary['maximum_power_balance_residual_pu']]
    check['network_balance_passed']=all(math.isfinite(v) and 0<=v<=1e-8 for v in checks)
    check['passed']=bool(check['passed'] and check['network_balance_passed'])
    return check


def fresh(name,role,out,mode,anchor=None,reverse=False):
    args=[sys.executable,'-m','benchmarks.network_dynamic_qualification','--worker',name,'--role',role,'--mode',mode,'--out',str(out)]
    if anchor:args+=['--anchor',str(anchor)]
    if reverse:args+=['--reverse']
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    result=subprocess.run(args,env=env,capture_output=True,text=True,timeout=660)
    out.with_suffix('.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError('fresh worker failed: '+result.stderr[-2500:])
    metrics=read_json(out/'metrics.json');trace=read_json(out/'trace.json')
    if metrics.get('completed') is not True or trace.get('completed') is not True:raise ValueError('worker returned incomplete trajectory: '+str(trace.get('status')))
    return metrics,trace


def protocol(names):
    return dict(cases=list(names),repeats=REPEATS,modes=list(MODES),producer=identity(),threads=1,affinity='first available CPU per worker',fresh_process_per_run=True,timeout_s=660,scope='separate RMS scale, 45s island, cascade/transfer, and independent-branch phase tracks; not all features combined at largest size')


def assessment_row(name,checks,measurements,failure=None):
    modes=('radau',) if name in PHASE_NAMES else MODES
    resources={mode:dict(wall_median_s=statistics.median(m['wall_s'] for m in measurements if m['mode']==mode),cpu_median_s=statistics.median(m['cpu_s'] for m in measurements if m['mode']==mode),peak_rss_max_kib=max(m['current_exec_peak_rss_kib'] for m in measurements if m['mode']==mode),dynamic_states=next(m['states'] for m in measurements if m['mode']==mode)) for mode in modes if any(m['mode']==mode for m in measurements)}
    return dict(case=name,passed=failure is None and bool(checks) and all(c['passed'] for c in checks),checks=checks,resources=resources,failure=failure)


def verify_result(root):
    root=Path(root);manifest=verify(root);p=read_json(root/'protocol.json');names=p['cases']
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('registered inventory required')
    if p!=protocol(names):raise ValueError('current protocol/producer differs')
    rows=[]
    def load(folder,name,mode,role,reverse=False):
        m=read_json(folder/'metrics.json');r=read_json(folder/'trace.json')
        wanted=phase_document(name) if name in PHASE_NAMES else study(name,mode,reverse).model_dump(mode='json')
        if read_json(folder/'study.json')!=wanted or m['study_sha256']!=digest(encoded(wanted)):raise ValueError('registered input differs')
        if m['producer']!=identity() or r['code_sha256']!=p['producer']['package']:raise ValueError('trace producer differs')
        if (m['case'],m['mode'],m['role'],m['reversed_equipment'])!=(name,mode,role,reverse):raise ValueError('measurement role differs')
        if not m['completed'] or not r['completed']:raise ValueError('incomplete worker')
        for key in ['wall_s','cpu_s','current_exec_peak_rss_kib']:
            if type(m[key]) not in (int,float) or not math.isfinite(m[key]) or m[key]<=0:raise ValueError('invalid measured resource')
        if len(m['cpu_affinity'])!=1:raise ValueError('one CPU per measured worker required')
        if role=='primary' and (type(m['states']) is not int or m['states']<=0):raise ValueError('dynamic state count missing')
        if role=='primary' and name not in PHASE_NAMES:
            expected='analytic_Schur_products' if mode=='network_dae' else 'column_blocks'
            if r['integration']['jacobian_assembly']!=expected:raise ValueError('wrong integration path')
        return m,r
    for name in names:
        folder=root/name;checks=[];measurements=[]
        modes=('radau',) if name in PHASE_NAMES else MODES
        for mode in modes:
            _,ref=load(folder/(mode+'-reference'),name,mode,'reference');previous=None
            for repeat in range(REPEATS):
                m,r=load(folder/(mode+'-'+str(repeat)),name,mode,'primary');measurements.append(m)
                if previous is not None and any(r[k]!=previous[k] for k in ['time_s','sample_side','channels','event_log']):raise ValueError('repeat trace differs')
                previous=r;checks.append(compare(name,None if name in PHASE_NAMES else study(name,mode),r,ref))
        if name in ISLAND_NAMES+AUTO_NAMES:
            _,r=load(folder/'reversed',name,'network_dae','primary',True)
            checks.append(compare(name,study(name),r,read_json(folder/'network_dae-reference'/'trace.json')))
        row=assessment_row(name,checks,measurements)
        if row!=read_json(folder/'assessment.json'):raise ValueError('stored numerical/resource assessment differs')
        rows.append(row)
    summary=read_json(root/'summary.json')
    if summary['producer']!=identity() or summary['rows']!=rows or summary['passed']!=all(r['passed'] for r in rows) or summary['full_declared_inventory_executed']!=(tuple(names)==NAMES) or summary['universal_scale_or_superiority'] is not False:raise ValueError('stored summary differs')
    if verify(root)!=manifest:raise ValueError('artifact changed during verification')
    return dict(verified=True,passed=summary['passed'],cases=len(rows),full_declared_inventory_executed=summary['full_declared_inventory_executed'],manifest_sha256=manifest)


def run(out,names=NAMES):
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('unique registered cases required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);before=protocol(names);(out/'protocol.json').write_bytes(encoded(before));rows=[]
    for name in names:
        folder=out/name;folder.mkdir();modes=('radau',) if name in PHASE_NAMES else MODES;checks=[];measurements=[];failure=None
        try:
            for mode in modes:
                previous=None
                for repeat in range(REPEATS):
                    metric,trace=fresh(name,'primary',folder/(mode+'-'+str(repeat)),mode)
                    if previous is not None and any(trace[k]!=previous[k] for k in ('time_s','sample_side','channels','event_log')):raise ValueError('repeat physical trace differs')
                    previous=trace;measurements.append(metric)
                _,ref=fresh(name,'reference',folder/(mode+'-reference'),mode,folder/(mode+'-0')/'trace.json')
                spec=None if name in PHASE_NAMES else study(name,mode)
                for repeat in range(REPEATS):
                    primary=read_json(folder/(mode+'-'+str(repeat))/'trace.json');checks.append(compare(name,spec,primary,ref))
            if name in ISLAND_NAMES+AUTO_NAMES:
                _,reversed_trace=fresh(name,'primary',folder/'reversed','network_dae',reverse=True)
                checks.append(compare(name,study(name),reversed_trace,read_json(folder/'network_dae-reference'/'trace.json')))
        except (ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:failure=str(exc);print(name,'failed',failure,flush=True)
        row=assessment_row(name,checks,measurements,failure)
        (folder/'assessment.json').write_bytes(encoded(row));rows.append(row);print(name,row['passed'],row['resources'],flush=True)
    if protocol(names)!=before:raise RuntimeError('qualification producer changed')
    result=dict(schema='power-grid-env.network-dynamic-qualification/v1',passed=all(r['passed'] for r in rows),rows=rows,full_declared_inventory_executed=tuple(names)==NAMES,producer=before['producer'],universal_scale_or_superiority=False)
    (out/'summary.json').write_bytes(encoded(result));seal(out);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path);p.add_argument('--verify',type=Path);p.add_argument('--case',choices=NAMES,action='append');p.add_argument('--worker',choices=NAMES);p.add_argument('--role',choices=['primary','reference']);p.add_argument('--mode',choices=MODES,default='network_dae');p.add_argument('--anchor');p.add_argument('--reverse',action='store_true')
    a=p.parse_args()
    if a.verify:r=verify_result(a.verify);print(r);raise SystemExit(0 if r['passed'] else 2)
    if not a.out:p.error('--out required for worker or new study')
    if a.worker:worker(a.worker,a.role,a.out,a.mode,a.anchor,a.reverse)
    else:raise SystemExit(0 if run(a.out,tuple(a.case) if a.case else NAMES)['passed'] else 2)
