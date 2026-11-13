"""Measured per-port RMS I²t; fault duty must not replace cable/CT duty."""
import math
from pathlib import Path

from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest


def integrate(time,values,start,end):
    if len(time)!=len(values) or len(time)<2 or not start<end:
        raise ValueError('invalid RMS duty arrays or window')
    if any(not math.isfinite(x) for x in [*time,*values]) or any(x<0 for x in values):
        raise ValueError('nonfinite or negative RMS data')
    if any(b<a for a,b in zip(time,time[1:])):raise ValueError('time reverses')
    indices=[i for i,t in enumerate(time) if start<=t<=end]
    if not indices or time[indices[0]]!=start or time[indices[-1]]!=end:
        raise ValueError('exact event boundaries required')
    total=sum((time[j]-time[i])*(values[i]**2+values[i]*values[j]+values[j]**2)/3 for i,j in zip(indices,indices[1:]))
    return dict(i2t_a2s=total,maximum_sampled_rms_a=max(values[i] for i in indices))


def measured(study,report):
    c=study['coupled'];n=study['network'];fault=c['faults'][0]
    start,end=fault['apply_s'],fault['clear_s'];ch=report['channels'];times=report['time_s'];sides=report['sample_side']
    if len(times)!=len(sides):raise ValueError('missing event sides')
    for t in [start,end]:
        if [sides[i] for i,x in enumerate(times) if x==t]!=['pre_event','post_event']:
            raise ValueError('exact one-sided fault boundaries required')
    source_relay=next(r for r in c['network_automation']['relays'] if r['measurement_kind']=='source')
    if any(v!=1 for v in ch[source_relay['id']+'.measurement_valid']):raise ValueError('invalid source measurement')
    line=n['lines'][0]['id']
    arrays=dict(source=ch[source_relay['id']+'.measurement'],feeder_from=ch[line+'.from_current_a'],feeder_to=ch[line+'.to_current_a'],fault=ch[fault['id']+'.current_a'])
    for m in c['motors']:
        scale=m['rated_va']/(math.sqrt(3)*m['rated_ll_v'])
        arrays['motor:'+m['id']]=[x*scale for x in ch[m['id']+'.current_pu']]
    return {name:integrate(times,values,start,end) for name,values in arrays.items()}


def qualify(source,out):
    source=Path(source);before=verify(source)
    summary=read_json(source/'summary.json')
    if not summary['passed']:raise ValueError('qualified dynamic source required')
    rows=[];by_key={}
    for entry in summary['rows']:
        child=source/entry['id'];verify(child)
        study=read_json(child/'study.json');report=read_json(child/'trace.json')
        ports=measured(study,report);name=entry['id'];profile,method,grid=name.rsplit('-',2)
        key=(profile,method);by_key.setdefault(key,{})[int(grid.removeprefix('grid'))]=ports
        rows.append(dict(id=name,ports=ports))
    convergence=[]
    for (profile,method),grids in by_key.items():
        if set(grids)!={0,1,2}:raise ValueError('three complete grids required')
        differences={k:abs(grids[2][k]['i2t_a2s']-grids[1][k]['i2t_a2s'])/max(grids[2][k]['i2t_a2s'],1e-9) for k in grids[2]}
        convergence.append(dict(profile=profile,method=method,relative_changes=differences,passed=max(differences.values())<=.002))
    if verify(source)!=before:raise ValueError('source changed')
    result=dict(passed=all(r['passed'] for r in convergence),source_manifest_sha256=before,
                profiles=len(convergence),measured_runs=len(rows),rows=rows,convergence=convergence,
                producer_sha256=digest(Path(__file__).read_bytes()),normative_approval=False,
                scope='Sampled squared-linear RMS integration across exact one-sided fault and motor-open events. Fixed impedances; no DC peak, thermal feedback, ratings acceptance, or contribution-I2t additivity. Maximum RMS is sampled, not a continuous peak.')
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    (root/'result.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=qualify(a.source,a.out);print(dict(passed=r['passed'],measured_runs=r['measured_runs']))
    if not r['passed']:raise SystemExit(2)
