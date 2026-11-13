"""Sampled RMS cable/device duty in the declared fixed-impedance fault cases.

No making peak, DC offset, temperature feedback or OEM rating validation.
"""
import argparse,math,time
from pathlib import Path
from power_grid_env.protection_envelope_store import verify_store
from power_grid_env.protection_envelope import effective_plans
from power_grid_env.closed_loop_protection import compile_closed_loop
from power_grid_env.study_artifacts import read_json,encoded,seal,verify,digest
from power_grid_env.evidence import package_fingerprint


def duty(plan,kind,report):
    if kind not in ['primary','breaker_failure','measurement_loss']:raise ValueError('fault duty requires a declared fault scenario')
    cb='primary-cb' if kind=='primary' else 'backup-cb'
    events=[e for e in report['event_log'] if e['kind']=='breaker_open' and e.get('equipment_id')==cb and e.get('outcome')=='applied']
    if len(events)!=1:raise ValueError('one applied clearing action required')
    end=events[0]['time_s'];start=plan.fault_apply_s
    if not start<end<plan.fault_clear_s:raise ValueError('clearing must occur while fault persists')
    n=plan.equipment.mandatory_cases[0].study.network;line=n.lines[0].id
    times=report['time_s'];sides=report['sample_side'];ch=report['channels']
    line_current=ch[line+'.from_current_a'];source_current=ch['backup-oc.measurement']
    if len(times)!=len(sides) or any(len(a)!=len(times) for a in [line_current,source_current,ch['backup-oc.measurement_valid']]):raise ValueError('duty trace length differs')
    if any(not math.isfinite(v) for v in [*times,*line_current,*source_current]) or any(v<0 for v in [*line_current,*source_current]):raise ValueError('nonfinite or negative RMS duty input')
    indices=[i for i,t in enumerate(times) if start-1e-12<=t<=end+1e-12]
    if not indices or abs(times[indices[0]]-start)>1e-8 or abs(times[indices[-1]]-end)>1e-8:raise ValueError('fault duty endpoints absent')
    if any(ch['backup-oc.measurement_valid'][i]!=1 for i in indices):raise ValueError('source-current measurement invalid')
    i2t=0.
    for i,j in zip(indices,indices[1:]):
        dt=times[j]-times[i]
        if dt<0:raise ValueError('fault duty time reverses')
        # Exact integral of squared linear current; duplicate event samples
        # have zero width, preventing smoothing of opening/fault jumps.
        i2t+=dt*(line_current[i]**2+line_current[i]*line_current[j]+line_current[j]**2)/3
    before=[i for i in indices if abs(times[i]-end)<=1e-8 and sides[i]=='pre_event']
    if len(before)!=1:raise ValueError('one pre-opening measurement required')
    measurement=line_current if cb=='primary-cb' else source_current
    interrupting=measurement[before[0]]
    sizing=plan.equipment.sizing
    cable=next(c for c in sizing.cables if c.id==plan.selection.cable_id)
    transformer=next(t for t in sizing.transformers if t.id==plan.selection.transformer_id)
    cable_limit=(cable.k_a_sqrt_s_mm2*cable.area_mm2)**2
    duration=end-start;peak=max(source_current[i] for i in indices)
    setting=plan.primary if cb=='primary-cb' else plan.backup
    interrupt_limit=min(setting.interrupting_a,sizing.installation.breaker_interrupting_a) if cb=='primary-cb' else setting.interrupting_a
    checks=dict(primary_owner_clearing_budget=kind!='primary' or duration<=sizing.installation.clearing_s+plan.event_time_tolerance_s,
        cable_adiabatic=duration<=5 and i2t<=cable_limit,
        device_interrupting=interrupting<=interrupt_limit,
        transformer_short_time=duration<=transformer.short_time_s and peak<=transformer.short_time_a)
    return dict(passed=all(checks.values()),checks=checks,clearing_s=duration,cable_i2t_a2s=i2t,cable_limit_i2t_a2s=cable_limit,
        pre_opening_rms_a=interrupting,interrupting_limit_a=interrupt_limit,max_sampled_transformer_rms_a=peak,
        transformer_limit_a=transformer.short_time_a,transformer_limit_s=transformer.short_time_s,
        scope='fault-application to actual opening, squared linear RMS-current interpolants; fixed R/X and owner-supplied ratings, no electrothermal feedback')


def run(radau,dae,owner_radau,owner_dae,out,*,wait_for_stores=False):
    root=Path(out);root.mkdir(parents=True,exist_ok=False);rows=[];indices=[]
    deadline=time.monotonic()+7200
    for method,source,owner in [('radau',Path(radau),owner_radau),('network_dae',Path(dae),owner_dae)]:
        while not (source/'index/manifest.json').exists():
            if not wait_for_stores or time.monotonic()>deadline:raise ValueError('sealed full store required')
            time.sleep(.2)
        verified=verify_store(source,expected_envelope=owner)
        indices.append(dict(method=method,index_sha256=verified['index_manifest_sha256']))
        for name,plan,recipe in effective_plans(owner):
            _,pack,kinds,_=compile_closed_loop(plan)
            for case in pack.cases:
                kind=kinds[case.id]
                if kind=='normal':continue
                grids=[duty(plan,kind,read_json(source/'studies'/name/'studies'/case.id/f'grid-{level}'/'result.json')) for level in range(3)]
                relative=abs(grids[2]['cable_i2t_a2s']-grids[1]['cable_i2t_a2s'])/max(grids[2]['cable_i2t_a2s'],1e-12)
                time_change=abs(grids[2]['clearing_s']-grids[1]['clearing_s'])
                stable=relative<=.002 and time_change<=1e-7
                rows.append(dict(method=method,corner=name,case=case.id,grids=grids,fine_i2t_relative_change=relative,fine_clear_time_change_s=time_change,
                    passed=stable and all(g['passed'] for g in grids)))
        print(method,'measured duty checks complete',flush=True)
    result=dict(passed=all(r['passed'] for r in rows),code_sha256=package_fingerprint(),source_indices=indices,rows=rows,
        fault_grid_checks=3*len(rows),i2t_fine_pair_limit=.002,clear_time_fine_pair_limit_s=1e-7,
        normative_approval=False,manufacturer_ratings_authenticated=False,continuous_extrema_qualified=False,
        cold_hot_envelope_qualified=False,producer_sha256=digest(Path(__file__).read_bytes()))
    (root/'summary.json').write_bytes(encoded(result));seal(root);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--radau',required=True);p.add_argument('--dae',required=True);p.add_argument('--owner-radau',required=True);p.add_argument('--owner-dae',required=True);p.add_argument('--out',required=True);p.add_argument('--wait-for-stores',action='store_true');a=p.parse_args()
    r=run(a.radau,a.dae,read_json(a.owner_radau),read_json(a.owner_dae),a.out,wait_for_stores=a.wait_for_stores)
    print(encoded(dict(passed=r['passed'],fault_grid_checks=r['fault_grid_checks'])).decode());raise SystemExit(0 if r['passed'] else 2)
