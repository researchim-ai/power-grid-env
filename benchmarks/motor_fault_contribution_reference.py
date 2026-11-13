"""Native dynamic snapshots vs phase-preserving motor fault attribution.

This is an algebraic measurement audit conditioned on simulated rotor EMFs,
plus Radau/DAE agreement; it is not an independent motor ODE reference.
"""
import copy
import math
from pathlib import Path

import numpy as np
from power_grid_env.closed_loop_protection import compile_closed_loop
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.motor_fault_contribution import snapshot_from_report,calculate
from power_grid_env.study_artifacts import read_json,encoded,seal,digest
from benchmarks.closed_loop_protection_reference import compare_solvers


def audit(study,report):
    if not report.get('completed') or not report.get('solved'):
        raise ValueError('completed solved dynamic trace required')
    fault=study.coupled.faults[0];ch=report['channels']
    indices=[i for i,a in enumerate(ch[fault.id+'.active']) if a==1]
    if not indices:raise ValueError('missing active-fault trace coverage')
    # Include first post-apply and last pre-clear, with five interior points.
    chosen=sorted(set(indices[int(i)] for i in np.linspace(0,len(indices)-1,7)))
    rows=[]
    for i in chosen:
        snapshot=snapshot_from_report(study,report,i);r=calculate(snapshot)
        vb=study.network.voltage_levels[0].nominal_ll_v/math.sqrt(3)
        from power_grid_env.topology import compile_topology
        top=compile_topology(study.network)
        source=study.network.sources[0];line=study.network.lines[0]
        buses={'upstream':top.terminal_bus[source.terminal_id], 'downstream':top.terminal_bus[line.terminal_to]}
        errors={}
        for name,bus in buses.items():
            actual=ch[bus+'.v'][i]*vb*np.exp(1j*ch[bus+'.a'][i])
            predicted=complex(r['voltages_v'][name]['real'],r['voltages_v'][name]['imag'])
            errors[name+'_v']=float(abs(actual-predicted))
        errors['fault_a']=abs(r['currents']['fault_a']['magnitude']-ch[fault.id+'.current_a'][i])
        for m in study.coupled.motors:
            ib=m.rated_va/(math.sqrt(3)*m.rated_ll_v)
            errors[m.id+'_a']=abs(r['currents']['motor:'+m.id+'_a']['magnitude']-ch[m.id+'.current_pu'][i]*ib)
        for relay in study.coupled.network_automation.relays:
            key='source_a' if relay.measurement_kind=='source' else 'feeder_from_a'
            errors[relay.id+'_a']=abs(r['currents'][key]['magnitude']-ch[relay.id+'.measurement'][i])
        rows.append(dict(index=i,time_s=report['time_s'][i],sample_side=report['sample_side'][i],errors=errors,
                         attribution=r,passed=r['passed_algebra_audits'] and max(errors.values())<=2e-5))
    return dict(passed=all(r['passed'] for r in rows),sample_count=len(rows),rows=rows)


def studies(plan):
    _,pack,_,_=compile_closed_loop(plan)
    base=pack.cases[0].study.model_dump(mode='json')
    for label,temp,factor,upstream,open_motor in [
        ('cold-running',20,1,False,False),('hot-low-running',90,.95,False,False),
        ('hot-upstream',90,1,True,False),('hot-motor-open',90,1,False,True)]:
        for method in ['radau','network_dae']:
            for grid in range(3):
                s=copy.deepcopy(base);c=s['coupled'];n=s['network']
                c.update(duration_s=3.3,step_s=.002/2**grid,integration_method=method,
                         events=[dict(time_s=3.05,kind='motor_open',equipment_id=c['motors'][0]['id'])] if open_motor else [])
                # Monitor with real CTs, but keep pickups beyond this case's
                # range so that the fault is cleared by the scheduled event.
                for relay in c['network_automation']['relays']:relay['pickup']=1e8
                line=n['lines'][0]
                resistance=.193*(1+.00393*(temp-20))*1.05*.1
                line['r_ohm']=[[resistance if i==j else 0 for j in range(3)] for i in range(3)]
                n['sources'][0]['ll_v']*=factor
                nodes={t['id']:t['connectivity_node_id'] for t in n['terminals']}
                node=nodes[n['sources'][0]['terminal_id']] if upstream else c['motors'][0]['node_id']
                c['faults']=[dict(id='attribution-fault',node_id=node,apply_s=3,clear_s=3.15,r_ohm=.001,x_ohm=0)]
                s['id']='attribution-'+label
                yield label,method,grid,StudySpec.model_validate(s)


def qualify(plan,out):
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    rows=[];reports={};pairs=[]
    for label,method,grid,study in studies(plan):
        name=f'{label}-{method}-grid{grid}';print(name,flush=True)
        report=run_study(study)
        checked=audit(study,report)
        child=root/name;child.mkdir()
        for key,value in [('study',study.model_dump(mode='json')),('trace',report),('audit',checked)]:
            (child/(key+'.json')).write_bytes(encoded(value))
        seal(child)
        rows.append(dict(id=name,passed=checked['passed'],snapshots=checked['sample_count']))
        reports[(label,method,grid)]=report
        if method=='network_dae':
            pair=compare_solvers(reports[(label,'radau',grid)],report)
            pairs.append(dict(profile=label,grid=grid,**pair))
    result=dict(passed=all(r['passed'] for r in rows) and all(p['passed'] for p in pairs),
                native_runs=len(rows),snapshot_audits=sum(r['snapshots'] for r in rows),rows=rows,solver_pairs=pairs,
                reference_sha256=digest(Path(__file__).read_bytes()),
                scope='24 native dynamic runs, four authored scenarios and three grids on each solver. Conditional frozen-EMF algebra/CT audit, not an independent dynamic trajectory or general motor fault duty envelope.')
    (root/'owner-plan.json').write_bytes(encoded(plan));(root/'summary.json').write_bytes(encoded(result));seal(root)
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=qualify(read_json(a.plan),a.out)
    print({k:r[k] for k in ['passed','native_runs','snapshot_audits']})
    if not r['passed']:raise SystemExit(2)
