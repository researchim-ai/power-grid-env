"""Closed feedback of native ESST1A and native MNLEX2 with explicit event sides."""
from pathlib import Path
import copy,json,math
import numpy as np
from benchmarks.generator_esst1a_joint_cases import NAMES,NEGATIVE,example
from benchmarks.generator_esst1a_validation import compare as compare_network
from benchmarks.esst1a_network import run
from power_grid_env.study_spec import run_study
from power_grid_env.report import write_json

EVENT_LIMIT=1e-6
STATE_LIMIT=2e-5
RESET_RESOLUTION=1e-9


def significant_resets(report,native=False):
    kind='native_mnlex2_lag_reset' if native else 'uel_lag_reset'
    events=[e for e in report['event_log'] if e['kind']==kind]
    for e in events:
        if not all(math.isfinite(e[k]) for k in ('time_s','raw_state_before_pu','raw_state_after_pu')):
            raise ValueError('nonfinite UEL reset evidence')
        if e['bound'] not in ('lower','upper'):raise ValueError('unknown UEL reset bound')
    return [e for e in events if abs(e['raw_state_before_pu']-e['raw_state_after_pu'])>RESET_RESOLUTION]


def coalesce_zero_time_batches(report):
    """Keep the first pre-side and last post-side of a <=1 ps event batch."""
    r=copy.deepcopy(report);keep=[];i=0
    while i<len(r['time_s']):
        j=i+1
        while j<len(r['time_s']) and r['time_s'][j]-r['time_s'][i]<1e-12:j+=1
        pre=[k for k in range(i,j) if r['sample_side'][k]=='pre_event']
        post=[k for k in range(i,j) if r['sample_side'][k]=='post_event']
        if pre:keep.append(pre[0])
        if post:keep.append(post[-1])
        if not pre and not post:keep.append(j-1)
        i=j
    for key in ['time_s','sample_side']:r[key]=[r[key][i] for i in keep]
    for key in r['channels']:r['channels'][key]=[r['channels'][key][i] for i in keep]
    if 'node_voltage' in r:
        for node in r['node_voltage'].values():
            for key in node:node[key]=[node[key][i] for i in keep]
    return r


def _causal_closure(spec,primary,native,left,right,budget):
    """Observe actual closure samples on copies; never modify saved traces."""
    a,b=copy.deepcopy(primary),copy.deepcopy(native);alignment=[]
    for e in spec.coupled.events:
        command=e.time_s
        if not any(abs(p['time_s']-command)<=budget and abs(q['time_s']-command)<=budget for p in left for q in right if p['equipment_id']==q['equipment_id']):continue
        for label,original,edited in [('primary',primary,a),('native',native,b)]:
            post=[i for i,(t,side) in enumerate(zip(original['time_s'],original['sample_side'])) if side=='post_event' and abs(t-command)<=1e-12]
            closure=[i for i,(t,side) in enumerate(zip(original['time_s'],original['sample_side'])) if side=='post_event' and command-1e-12<=t<=command+budget]
            if not post or not closure:raise ValueError('missing actual command/reset event-side sample')
            i=post[-1];j=closure[-1]
            for key in edited['channels']:edited['channels'][key][i]=original['channels'][key][j]
            if 'node_voltage' in edited:
                for node in edited['node_voltage'].values():
                    for values in node.values():values[i]=values[j]
            alignment.append(dict(trace=label,command_time_s=command,sampled_time_s=original['time_s'][j],command_sample_index=i,closure_sample_index=j))
    return a,b,alignment


def _network_check(spec,a,b):
    result=compare_network(spec,a,b)
    required={p.generator_id+'.UEL.'+k for p in spec.coupled.underexcitation_limiters for k in ['p_pu','q_pu','voltage_pu','circle_error_pu2','raw_lag_state_pu','feedback_memory_pu','feedback_pu','output_pu']}
    if not required<=result['errors'].keys():raise ValueError('missing native UEL internal/physical channels')
    return result


def compare(spec,primary,native):
    if not native.get('native_MNLEX2_in_feedback'):raise ValueError('actual native UEL feedback required')
    primary=coalesce_zero_time_batches(primary);native=coalesce_zero_time_batches(native)
    ids={p.generator_id for p in spec.coupled.underexcitation_limiters}
    left=significant_resets(primary);right=significant_resets(native,True)
    unknown=({e['equipment_id'] for e in left+right}-ids)
    if unknown:raise ValueError('reset for undeclared UEL: '+str(sorted(unknown)))
    reset_checks=[];failures=[];inventory=[]
    for gid in sorted(ids):
        x=[e for e in left if e['equipment_id']==gid];y=[e for e in right if e['equipment_id']==gid]
        inventory.append(dict(generator_id=gid,primary_resets=len(x),native_resets=len(y)))
        if len(x)!=len(y):
            failures.append(dict(reason='native_UEL_reset_inventory_differs',generator_id=gid,primary_resets=len(x),native_resets=len(y)))
        for p,q in zip(x,y):
            dt=abs(p['time_s']-q['time_s']);jump_error=max(abs(p[k]-q[k]) for k in ['raw_state_before_pu','raw_state_after_pu'])
            finite=all(math.isfinite(e[k]) for e in (p,q) for k in ('time_s','raw_state_before_pu','raw_state_after_pu'))
            good=finite and p['bound']==q['bound'] and dt<=EVENT_LIMIT and jump_error<=STATE_LIMIT
            reset_checks.append(dict(generator_id=gid,primary_time_s=p['time_s'],native_time_s=q['time_s'],time_error_s=dt,raw_jump_error_pu=jump_error,bound=p['bound'],passed=good))
            if not good:failures.append(dict(reason='native_UEL_reset_sides_differ',generator_id=gid,event_time_error_s=dt,raw_jump_error_pu=jump_error,primary=p,native=q))
    a,b,alignment=_causal_closure(spec,primary,native,left,right,EVENT_LIMIT)
    result=_network_check(spec,a,b)
    result.update(native_UEL_reset_checks=reset_checks,reset_inventory=inventory,reset_failures=failures,reset_audit_complete=True,reset_resolution_pu=RESET_RESOLUTION,event_limit_s=EVENT_LIMIT,command_causal_closure=alignment,sampling='original declared grid; matched command/reset causal closure within 1 us; original traces preserved')
    if failures:
        # Keep the first-failure API, but retain ALL reset and physical checks.
        result.update(failures[0]);result['passed']=False
        # A wider observation window is diagnostic only: it cannot qualify a
        # trajectory. It detects unrelated physics errors in the negative witness.
        da,db,observations=_causal_closure(spec,primary,native,left,right,1e-5)
        diagnostic=_network_check(spec,da,db)
        diagnostic.update(observation_window_s=1e-5,command_causal_closure=observations,qualifies_trajectory=False)
        result['negative_witness_physics_diagnostic']=diagnostic
    return result


def expected_rejection(name,check):
    failures=check.get('reset_failures',[])
    return (name in NEGATIVE and not check['passed'] and check.get('reset_audit_complete') is True
        and bool(failures) and check.get('negative_witness_physics_diagnostic',{}).get('passed') is True
        and all(f.get('reason')=='native_UEL_reset_sides_differ' and f.get('generator_id')=='g'
            and EVENT_LIMIT<f.get('event_time_error_s',0.)<1e-5
            and math.isfinite(f.get('raw_jump_error_pu',math.inf)) and f['raw_jump_error_pu']<=STATE_LIMIT
            and f.get('primary',{}).get('bound')==f.get('native',{}).get('bound')=='lower'
            for f in failures))


def qualify(out,root,names=NAMES,tolerances=(1e-11,1e-12)):
    from power_grid_env.evidence import package_fingerprint
    from benchmarks.native_online_validation import source_identity
    from benchmarks.mnlex2_model_exchange import verify
    if not names or len(set(names))!=len(names) or not set(names)<=set(NAMES):raise ValueError('unique registered joint cases required')
    if not tolerances or any(not math.isfinite(t) or not 0<t<=1e-6 for t in tolerances):raise ValueError('positive finite tolerances required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();sources=source_identity();identity=verify(root);rows=[]
    write_json(out/'protocol.json',dict(code_sha256=code,source_sha256=sources,reference=identity,cases={n:example(n).model_dump(mode='json') for n in names},native_tolerances=list(tolerances),event_limit_s=EVENT_LIMIT,state_limit_pu=STATE_LIMIT,reset_resolution_pu=RESET_RESOLUTION,source_states_or_event_clocks_imported=False))
    write_json(out/'summary.json',dict(passed=False,status='running'))
    for name in names:
        s=example(name);directory=out/name;directory.mkdir();a=run_study(s);write_json(directory/'primary.json',a)
        if not a['completed']:raise ValueError('primary joint case failed: '+name)
        for tolerance in tolerances:
            print(name,tolerance,flush=True);b=run(s,directory/str(tolerance),root,tolerance,native_uels=True,uel_event_policy='broadcast',uel_root_policy='compound_and_atoms');c=compare(s,a,b)
            write_json(directory/str(tolerance)/'reference.json',b);write_json(directory/str(tolerance)/'comparison.json',c);rows.append(dict(case=name,tolerance=tolerance,expected_agreement=name not in NEGATIVE,**c))
    if package_fingerprint()!=code or source_identity()!=sources or verify(root)!=identity:raise ValueError('joint reference producer changed')
    result=dict(passed=all((r['passed'] if r['expected_agreement'] else expected_rejection(r['case'],r)) for r in rows),all_registered_trajectories_agree=all(r['passed'] for r in rows),positive_cases=len(set(names)-set(NEGATIVE)),negative_cases=len(set(names)&set(NEGATIVE)),runs=rows,code_sha256=code,source_sha256=sources,native_AVR_and_UEL_in_closed_feedback=True,full_native_OEL_UEL_PSS_matrix_qualified=False,industrial_profile_qualified=False)
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path);p.add_argument('--out',required=True,type=Path);p.add_argument('--cases',nargs='+',choices=NAMES,default=NAMES);args=p.parse_args();r=qualify(args.out,args.root,args.cases);print(json.dumps(r,indent=2));raise SystemExit(0 if r['passed'] else 1)
