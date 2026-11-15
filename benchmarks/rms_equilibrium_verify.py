"""Offline replay of stationary roots, physical spectra, trajectories and refusals."""
import copy
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from benchmarks.rms_equilibrium_cases import jobs,negatives
from benchmarks.rms_equilibrium_reference import StationaryReference
from benchmarks.rms_equilibrium_acceptance import producers
from benchmarks.rms_winding_acceptance import compare
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import read_json,encoded,seal,verify


def packet(args):
    j,path=args;path=Path(path);anchor=verify(path);stored=read_json(path/'summary.json')
    power_errors=[]
    if 'reason' in j:
        if read_json(path/'study.json')!=j['study']:raise ValueError('negative owner differs')
        actual=run_study(StudySpec.model_validate(j['study']));previous=read_json(path/'trace.json')
        if actual['completed'] or actual.get('reason')!=j['reason'] or previous['completed'] or previous.get('reason')!=j['reason'] or 'time_s' in actual:raise ValueError('initial rejection replay differs')
    else:
        refs=[StationaryReference(j['study']).run(max_step=.004),StationaryReference(j['study']).run(max_step=.002,rtol=5e-12,atol=5e-13)]
        for name,ref in zip(['reference','refined-reference'],refs):
            saved=read_json(path/(name+'.json'))
            if saved['events']!=ref.events or saved['initialization']!=ref.initial:raise ValueError('stationary reference root/event replay differs')
        rows=[]
        for method in ['radau','network_dae']:
            for grid in ([0] if j['kind']=='hold' else [0,1]):
                child=path/(method+'-'+str(grid));verify(child)
                owner=copy.deepcopy(j['study']);owner['coupled'].update(integration_method=method,step_s=.02 if j['kind']=='hold' else .002/2**grid)
                spec=StudySpec.model_validate(owner);result=read_json(child/'trace.json')
                if read_json(child/'study.json')!=spec.model_dump(mode='json') or result['settings']!=spec.coupled.model_dump(mode='json') or result['network_sha256']!=canonical_hash(spec.network) or result['code_sha256']!=package_fingerprint():raise ValueError('native owner/network/runtime binding differs')
                ch=result['channels'];m=spec.coupled.motors[0];h=m.winding_thermal
                ps,pr=ch['m.stator_copper_loss_w'][0],ch['m.rotor_copper_loss_w'][0]
                shaft=m.rated_va*ch['m.load_torque_pu'][0]*ch['m.speed_pu'][0]
                ambient=h.stator_ambient_w_per_k*(ch['m.stator_temperature_c'][0]-h.ambient_c)+h.rotor_ambient_w_per_k*(ch['m.rotor_temperature_c'][0]-h.ambient_c)
                power_errors.extend([abs(ch['m.p_w'][0]-ps-pr-shaft),abs(ps+pr-ambient)])
                if max(power_errors)>1e-5:raise ValueError('stationary active power or heat-removal identity failed')
                checks=[compare(ref,result) for ref in refs]
                if checks!=read_json(child/'comparisons.json'):raise ValueError('independent comparison replay differs')
                op=result['operating_point']
                maximum=max(abs(ch['m.stator_net_heat_w'][0]),abs(ch['m.rotor_net_heat_w'][0]))
                if maximum>max(1e-7,1e-10*m.rated_va) or abs(maximum-op['maximum_stationary_heat_error_w'])>1e-8:raise ValueError('individual heat-power equilibrium certificate differs')
                initial=op['motor_initial']['m'];ref=refs[0].initial
                errors={k:abs(initial[k]-ref[k]) for k in ['speed_pu','current_pu','stator_temperature_c','rotor_temperature_c']}
                a=np.array([complex(*z) for z in op['physical_eigenvalues_per_s']]);b=np.array([complex(*z) for z in ref['eigenvalues_per_s']]);ix,iy=linear_sum_assignment(abs(a[:,None]-b[None,:]));spectral=float(max(abs(a[ix]-b[iy])))
                drift={}
                if j['kind']=='hold':drift={k:float(max(abs(np.array(result['channels']['m.'+k])-result['channels']['m.'+k][0]))) for k in ['e_real_pu','e_imag_pu','speed_pu','thermal_level','stator_temperature_c','rotor_temperature_c']}
                passed=all(c['passed'] for c in checks) and max(errors.values())<1e-6 and spectral<1e-5 and op['checked_dynamic_residual_per_s']<1e-8 and all(v<1e-7 for v in drift.values())
                rows.append(dict(method=method,grid=grid,passed=bool(passed),checks=[{k:v for k,v in c.items() if k!='values'} for c in checks],initial_errors=errors,spectrum_error_per_s=spectral,hold_drift=drift))
        if rows!=stored['rows'] or not all(r['passed'] for r in rows):raise ValueError('stationary/spectral/hold gate differs')
        if any(stored[k]!=j[k] for k in ['id','kind','condition','seed']):raise ValueError('case declaration differs')
    if not stored['passed'] or verify(path)!=anchor:raise ValueError('packet outcome/content changed')
    print('verified stationary',j['id'],flush=True)
    return dict(id=j['id'],passed=True,manifest_sha256=anchor,maximum_stationary_power_error_w=max(power_errors,default=0.))


def verify_corpus(source,out,workers=4):
    if not 1<=workers<=16:raise ValueError('workers must be1..16')
    source=Path(source);anchor=verify(source);s=read_json(source/'summary.json');hashes=producers();fp=package_fingerprint();positive=list(jobs());declared=positive+list(negatives())
    if not s['passed'] or s['code_sha256']!=fp or s['producers']!=hashes:raise ValueError('producer/outcome differs')
    if len(s['rows'])!=52 or {r['id'] for r in s['rows']}!={j['id'] for j in declared} or {p.name for p in source.iterdir()}!={j['id'] for j in declared}|{'summary.json','manifest.json'}:raise ValueError('coverage/layout differs')
    for j in declared:
        row=next(r for r in s['rows'] if r['id']==j['id'])
        if verify(source/j['id'])!=row['manifest_sha256'] or any(row[k]!=v for k,v in read_json(source/j['id']/'summary.json').items()):raise ValueError('index/packet binding differs')
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows=[f.result() for f in as_completed([pool.submit(packet,(j,str(source/j['id']))) for j in declared])]
    pairs=[]
    for j in positive:
        if j['seed'][0]!=20:continue
        other=next(k for k in positive if k['condition']==j['condition'] and k['kind']==j['kind'] and k['seed'][0]==90)
        a,b=[read_json(source/k['id']/'network_dae-0/trace.json') for k in [j,other]]
        errors={key:abs(a['channels']['m.'+key][0]-b['channels']['m.'+key][0]) for key in ['speed_pu','current_pu','e_real_pu','e_imag_pu','stator_temperature_c','rotor_temperature_c']}
        x,y=copy.deepcopy(j['study']),copy.deepcopy(other['study'])
        for p in [x,y]:p['coupled']['motors'][0]['winding_thermal']['initial_temperature_c']=[20.,20.]
        pairs.append(dict(id=j['id'],matched_physics=x==y,initial_errors=errors,passed=x==y and max(errors.values())<1e-7))
    if pairs!=s['seed_pairs'] or not all(p['passed'] for p in pairs):raise ValueError('seed independence gate differs')
    if verify(source)!=anchor or producers()!=hashes or package_fingerprint()!=fp:raise ValueError('source/producer changed')
    result=dict(passed=True,code_sha256=fp,producers=hashes,source_manifest_sha256=anchor,verified_packets=52,stored_native_trajectories=144,
        independent_reference_replays=80,native_initial_rejection_replays=12,maximum_stationary_power_error_w=max(r['maximum_stationary_power_error_w'] for r in rows),rows=sorted(rows,key=lambda r:r['id']),
        scope='Offline all independent root/spectrum/trajectory replay,owner/network/settings/producer binding;all12 native initial refusals rerun.No native positive-trajectory rerun,authentication,OEM or another-host claim.')
    root=Path(out);root.mkdir(parents=True,exist_ok=False);(root/'summary.json').write_bytes(encoded(result));seal(root);return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=4)
    r=verify_corpus(**vars(p.parse_args()));print(dict(passed=r['passed'],verified_packets=r['verified_packets']))
