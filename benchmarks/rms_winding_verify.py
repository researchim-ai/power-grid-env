"""Offline owner-bound replay of RMS winding reference and acceptance criteria."""
import copy
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path

import numpy as np

from benchmarks.rms_winding_cases import jobs
from benchmarks.rms_winding_reference import ElectrothermalReference
from benchmarks.rms_winding_acceptance import compare,producers
from power_grid_env.study_spec import StudySpec
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,verify,seal


def packet(args):
    j,path=args;path=Path(path);anchor=verify(path);stored=read_json(path/'summary.json')
    refs=[ElectrothermalReference(j['study']).run(max_step=.002),ElectrothermalReference(j['study']).run(max_step=.001,rtol=5e-12,atol=5e-13)]
    for name,ref in zip(['reference','refined-reference'],refs):
        previous=read_json(path/(name+'.json'))
        if previous['events']!=ref.events or previous['initialization']!=ref.initial:
            raise ValueError('independent event/initialization replay differs')
        for record in previous['regular_values']:
            actual=ref.sample(record['time_s'])
            if actual.keys()!=record['channels'].keys() or any(abs(actual[k]-record['channels'][k])>1e-9*(1+abs(actual[k])) for k in actual):
                raise ValueError('independent regular trajectory replay differs')
    checked=[]
    for method in ['radau','network_dae']:
        for grid in [0,1]:
            child=path/(method+'-'+str(grid));verify(child)
            owner=copy.deepcopy(j['study']);owner['coupled'].update(integration_method=method,step_s=.002/2**grid)
            spec=StudySpec.model_validate(owner)
            if read_json(child/'study.json')!=spec.model_dump(mode='json'):raise ValueError('native owner inputs differ')
            report=read_json(child/'trace.json')
            from power_grid_env.model import canonical_hash
            if report.get('code_sha256')!=package_fingerprint() or report.get('settings')!=spec.coupled.model_dump(mode='json') or report.get('network_sha256')!=canonical_hash(spec.network):raise ValueError('trace runtime/network/settings binding differs')
            checks=[compare(ref,report) for ref in refs]
            if checks!=read_json(child/'comparison.json') or not all(c['passed'] for c in checks):raise ValueError('replayed independent comparison differs')
            checked.append(dict(method=method,grid=grid,passed=True,checks=[{k:v for k,v in c.items() if k!='values'} for c in checks]))
    if checked!=stored['rows'] or not stored['passed'] or any(stored[k]!=j[k] for k in ['id','kind','initial','temperatures','feedback']):raise ValueError('packet declaration/derived criteria differ')
    a,b=refs
    from benchmarks.loaded_protection_winding_acceptance import event_key
    if [event_key(e) for e in a.events]!=[event_key(e) for e in b.events]:raise ValueError('refinement event sequence differs')
    event=max(abs(x['time_s']-y['time_s']) for x,y in zip(a.events,b.events))
    times=[i*.001 for i in range(601) if all(abs(i*.001-e['time_s'])>2e-7 for e in a.events+b.events)]
    deltas={k:max(abs(a.sample(t)[k]-b.sample(t)[k]) for t in times) for k in a.sample(0)}
    if event!=stored['refinement_event_error_s'] or deltas!=stored['refinement_errors'] or event>=1e-8 or any(v>=(1e-4 if k.endswith(('_loss_w','_heat_w','.current_a','.measurement')) else 1e-7) for k,v in deltas.items()):raise ValueError('refinement gate differs')
    if verify(path)!=anchor:raise ValueError('packet changed during verification')
    print('verified RMS winding',j['id'],flush=True)
    return dict(id=j['id'],passed=True,manifest_sha256=anchor)


def verify_corpus(source,out,workers=4):
    if not 1<=workers<=16:raise ValueError('workers must be1..16')
    source=Path(source);anchor=verify(source);s=read_json(source/'summary.json');fp=package_fingerprint();declared=list(jobs());hashes=producers()
    if not s['passed'] or s['code_sha256']!=fp or s['producers']!=hashes:raise ValueError('source producer/outcome differs')
    if len(s['rows'])!=32 or {r['id'] for r in s['rows']}!={j['id'] for j in declared} or {p.name for p in source.iterdir()}!={j['id'] for j in declared}|{'summary.json','manifest.json'}:raise ValueError('source coverage/layout differs')
    for j in declared:
        item=next(r for r in s['rows'] if r['id']==j['id'])
        if verify(source/j['id'])!=item['manifest_sha256'] or any(item[k]!=v for k,v in read_json(source/j['id']/'summary.json').items()):raise ValueError('index/packet binding differs')
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows=[f.result() for f in as_completed([pool.submit(packet,(j,str(source/j['id']))) for j in declared])]
    effects=[]
    for j in declared:
        if not j['feedback']:continue
        control=next(k for k in declared if not k['feedback'] and all(k[x]==j[x] for x in ['kind','initial','temperatures']))
        a,b=[read_json(source/k['id']/'network_dae-1/trace.json') for k in [j,control]]
        x,y=copy.deepcopy(j['study']),copy.deepcopy(control['study']);m=x['coupled']['motors'][0];h=m['winding_thermal'];ts,tr=h['initial_temperature_c']
        m['stator_r_pu']*=1+h['alpha_per_c']*(ts-h['reference_c']);m['rotor_r_pu']*=1+h['alpha_per_c']*(tr-h['reference_c']);h['alpha_per_c']=0.
        ids=['m.speed_pu','m.current_pu','m.e_real_pu','m.e_imag_pu','m.stator_temperature_c','m.rotor_temperature_c','m.stator_resistance_pu','m.rotor_resistance_pu']
        effects.append(dict(id=j['id'],matched_owner=x==y,initial_channel_error=float(max(abs(a['channels'][k][0]-b['channels'][k][0]) for k in ids)),current_magnitude_max_difference_pu=float(max(abs(np.array(a['channels']['m.current_pu'])-b['channels']['m.current_pu'])))))
    if effects!=s['matched_feedback_effects'] or not all(e['matched_owner'] and e['initial_channel_error']<1e-9 and e['current_magnitude_max_difference_pu']>1e-7 for e in effects):raise ValueError('frozen-initial-R effect gate differs')
    if verify(source)!=anchor or producers()!=hashes or package_fingerprint()!=fp:raise ValueError('source/producer changed during verification')
    result=dict(passed=True,source_manifest_sha256=anchor,code_sha256=fp,producers=hashes,verified_packets=32,native_traces=128,independent_reference_replays=64,rows=sorted(rows,key=lambda r:r['id']),scope='Offline full independent winding/clock trajectory replay,owner/network/settings binding,all numerical/refinement/matched-control gates recomputed.No native rerun,another-host or source authentication claim.')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);(root/'summary.json').write_bytes(encoded(result));seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=4)
    r=verify_corpus(**vars(p.parse_args()));print(dict(passed=r['passed'],verified_packets=r['verified_packets']))
