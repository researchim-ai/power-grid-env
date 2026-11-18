"""Bounded browser replay from owner-pinned, qualified balanced RMS records.

Branch flows are reconstructed from recorded complex bus voltages and physical
line impedances, not animation formulas. Source/motor values come from native
channels. Decimation keeps event sides and channel extrema; no interpolation.
"""
import argparse
import math
from pathlib import Path
import numpy as np
from power_grid_env.network_design import verify_network_design
from power_grid_env.network_design_view import observation
from power_grid_env.design_frame import extrema_indices
from power_grid_env.study_artifacts import read_json,encoded,verify,digest


def line_flow(parent,child,r,x):
    current=(parent-child)/complex(r,x)
    send=3*parent*np.conj(current);receive=3*child*np.conj(current)
    return dict(p_kw=send.real/1000,q_kvar=send.imag/1000,current_a=abs(current),
                loss_kw=(send.real-receive.real)/1000)


def keep_samples(report):
    t=report['time_s'];sides=report['sample_side']
    keep=set(range(0,len(t),20))|{0,len(t)-1}
    keep.update(i for i,s in enumerate(sides) if s in ('pre_event','post_event'))
    for key,value in report['channels'].items():
        if key.endswith(('.speed_pu','.current_pu','.bus_voltage_pu')) or key in ('source.p_w','source.q_var'):
            keep.update(extrema_indices(t,value,sides,buckets=80))
    return sorted(keep)


def series(study,report,graph):
    n=study['network'];motors=study['induction']['motors'];channels=report['channels']
    nominal=graph['nominal_ll_v'];vbase=nominal/math.sqrt(3)
    node_bus={m['node_id']:report['mapping']['motor_buses'][m['id']] for m in motors}
    buses={k[:-2] for k in channels if k.startswith('bus_') and k.endswith('.v')}
    remaining=buses-set(node_bus.values())
    if len(remaining)!=1:raise ValueError('one unmapped source bus required in this balanced profile')
    node_bus[graph['root_node']]=remaining.pop()
    volts={node:np.array(channels[bus+'.v'])*np.exp(1j*np.array(channels[bus+'.a']))*vbase for node,bus in node_bus.items()}
    if set(volts)!={v['node_id'] for v in graph['nodes']}:raise ValueError('node/voltage coverage differs')
    indices=keep_samples(report)
    def values(v):return [round(float(v[i]),6) for i in indices]
    result=dict(source=dict(p_kw=values(np.array(channels['source.p_w'])/1000),q_kvar=values(np.array(channels['source.q_var'])/1000)),
                nodes={},branches={},motors={})
    for node,v in volts.items():result['nodes'][node]=dict(voltage_pu=values(abs(v)/vbase))
    for branch in graph['branches']:
        line=next(l for l in n['lines'] if l['id']==branch['corridor_id'])
        r,x,b=[np.array(line[k]) for k in ('r_ohm','x_ohm','b_siemens')]
        if np.any(b) or not np.array_equal(r,np.eye(3)*r[0,0]) or not np.array_equal(x,np.eye(3)*x[0,0]):
            raise ValueError('balanced diagonal noncapacitive branch profile only')
        flow=line_flow(volts[branch['parent_node']],volts[branch['child_node']],r[0,0],x[0,0])
        if np.min(flow['loss_kw']) < -1e-8:raise ValueError('passive branch has negative loss')
        result['branches'][line['id']]={k:values(v) for k,v in flow.items()}
        result['branches'][line['id']]['loading_pct']=values(flow['current_a']/line['rating_a']*100)
        branch.update(rating_a=line['rating_a'],r_ohm=float(r[0,0]),x_ohm=float(x[0,0]))
    for m in motors:
        prefix=m['id']+'.';base=m['rated_va']/(math.sqrt(3)*m['rated_ll_v'])
        out={k:values(channels[prefix+k]) for k in ('connected','speed_pu','current_pu','bus_voltage_pu','thermal_level')}
        out['display_speed_pu']=[round(float(channels[prefix+'speed_pu'][i]),3) for i in indices]
        out['current_a']=values(np.array(channels[prefix+'current_pu'])*base)
        out['p_kw']=values(np.array(channels[prefix+'p_w'])/1000)
        out['q_kvar']=values(np.array(channels[prefix+'q_var'])/1000)
        result['motors'][m['id']]=out
        if np.max(abs(abs(volts[m['node_id']])/vbase-np.array(channels[prefix+'bus_voltage_pu'])))>1e-8:
            raise ValueError('recorded motor and reconstructed bus voltage differ')
    return indices,result


def build(source,source_manifest,out):
    if verify(source)!=source_manifest:raise ValueError('owner source manifest differs')
    source=Path(source);summary=read_json(source/'summary.json');designs=[]
    for item in summary['rows']:
        root=source/item['id'];checked=verify_network_design(root,expected_producer=summary['code_sha256'])
        if not checked['numerically_qualified']:raise ValueError('qualified physics required')
        task=read_json(root/'task.json');proposal=read_json(root/'proposal.json');graph=observation(task,proposal)
        plan=read_json(root/'plan.json');studies=[]
        for case in plan['cases']:
            leaf=root/'studies'/case['id']/'grid-2';study=read_json(leaf/'study.json');report=read_json(leaf/'result.json')
            indices,channels=series(study,report,graph)
            studies.append(dict(id=case['id'],time_s=[report['time_s'][i] for i in indices],sample_side=[report['sample_side'][i] for i in indices],
                series=channels,events=[{k:e[k] for k in ('time_s','kind','equipment_id') if k in e} for e in report['event_log']],
                trace_sha256=digest((leaf/'result.json').read_bytes()),source_samples=len(report['time_s']),display_samples=len(indices)))
        motors=plan['cases'][0]['study']['induction']['motors']
        tr=next(t for t in task['catalog']['transformers'] if t['id']==proposal['transformer_id'])
        designs.append(dict(id=item['id'],graph=graph,engineering_status=item['status'],
            transformer=dict(id=tr['id'],rating_kva=tr['rating_kva']['value']),
            motors=[{k:m[k] for k in ('id','node_id','rated_va','rated_ll_v','inertia_s')} for m in motors],studies=studies))
    result=dict(schema='power-grid-env.grid-studio/v1',scope='Recorded balanced RMS studies,one secondary-equivalent transformer. Not a live solver or EMT. Flow animation is symbolic.',
        provenance=dict(source_manifest_sha256=source_manifest,physics_code_sha256=summary['code_sha256'],
                        exporter_sha256=digest(Path(__file__).read_bytes()),sample_policy='Recorded samples; all event sides plus per-channel extrema and uniform anchors; six-decimal display export.'),designs=designs)
    if verify(source)!=source_manifest:raise ValueError('source changed')
    out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
    raw=encoded(result)+b'\n';out.write_bytes(raw)
    out.with_suffix('.manifest.json').write_bytes(encoded(dict(sha256=digest(raw),bytes=len(raw),source_manifest_sha256=source_manifest))+b'\n')
    print(dict(bytes=len(raw),designs=len(designs),studies=sum(len(d['studies']) for d in designs)))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for k in ('source','source-manifest','out'):p.add_argument('--'+k,required=True)
    build(**vars(p.parse_args()))
