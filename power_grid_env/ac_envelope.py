"""Observed voltage, terminal MVA and explicit pu-current limits on AC branches.

Monitoring does not trip apparatus or constrain the power-flow equations.
RATE_A is apparent power (MVA), never implicitly an ampere/current rating.
Only detected endpoint sign changes are bracketed; hidden excursions are not
excluded. The accepted branch can include a predictor overshoot past lambda=1.
"""
from pathlib import Path
import math,time
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.ac_pseudo_arc import PseudoArcOptions,make_family,correct,trace_power_flow,audit_trace
from power_grid_env.ac_continuation import physical_audit,ContinuationOptions,snapshot
from power_grid_env.balanced_ac import case_from_document
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


class TerminalCurrentLimit(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    branch_index:int=Field(ge=0)
    terminal:str=Field(pattern='^(from|to)$')
    maximum_pu:float=Field(gt=0,le=1e6)


class EnvelopeOptions(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    voltage_limits:bool=True
    apparent_power_limits:bool=True
    current_limits:list[TerminalCurrentLimit]=Field(default_factory=list,max_length=4096)
    margin_tolerance_pu:float=Field(default=1e-8,ge=0,le=1e-4)
    arc_tolerance:float=Field(default=1e-7,ge=1e-10,le=1e-3)
    maximum_events:int=Field(default=256,ge=1,le=1024)
    maximum_refinement_solves:int=Field(default=4096,ge=1,le=16384)
    refinement_iterations:int=Field(default=24,ge=1,le=32)
    maximum_wall_s:float=Field(default=120,gt=0,le=600)

    @model_validator(mode='after')
    def unique_currents(self):
        keys=[(x.branch_index,x.terminal) for x in self.current_limits]
        if len(keys)!=len(set(keys)):raise ValueError('duplicate terminal current limit')
        return self


def limits_for(case,o):
    limits=[]
    if o.voltage_limits:
        for i,b in enumerate(case.buses):
            for side,threshold in [('lower',b.vmin),('upper',b.vmax)]:
                limits.append(dict(key=f'bus:{b.id}:voltage:{side}',kind='voltage',side=side,bus_index=i,threshold=threshold,unit='pu'))
    if o.apparent_power_limits:
        for i,b in enumerate(case.branches):
            if b.status and b.rate_a>0:
                for terminal in ['from','to']:
                    limits.append(dict(key=f'branch:{i}:mva:{terminal}',kind='mva',branch_index=i,terminal=terminal,threshold=b.rate_a,unit='MVA'))
    for x in o.current_limits:
        if x.branch_index>=len(case.branches):raise ValueError('unknown branch for current limit')
        if not case.branches[x.branch_index].status:raise ValueError('explicit current limit on offline branch is not monitored')
        limits.append(dict(key=f'branch:{x.branch_index}:current:{x.terminal}',kind='current',branch_index=x.branch_index,terminal=x.terminal,threshold=x.maximum_pu,unit='pu'))
    return sorted(limits,key=lambda x:x['key'])


def terminal_values(case,result):
    """Complex terminal currents on the common Sbase, each terminal's pu Vbase."""
    v=np.asarray(result['vm_pu'])*np.exp(1j*np.deg2rad(result['va_deg']));idx={b.id:i for i,b in enumerate(case.buses)};rows=[]
    for b in case.branches:
        if not b.status:rows.append(dict(from_current=0.,to_current=0.,from_mva=0.,to_mva=0.));continue
        vf,vt=v[idx[b.fbus]],v[idx[b.tbus]];y=1/complex(b.r,b.x)
        tap=(b.ratio or 1.)*np.exp(1j*np.deg2rad(b.angle_deg))
        src=(y+1j*b.b/2)/abs(tap)**2*vf-y/np.conj(tap)*vt
        dst=(y+1j*b.b/2)*vt-y/tap*vf
        rows.append(dict(from_current=float(abs(src)),to_current=float(abs(dst)),
            from_mva=float(abs(vf*np.conj(src))*case.base_mva),to_mva=float(abs(vt*np.conj(dst))*case.base_mva)))
    return rows


def measure(case,result,limits):
    rows=terminal_values(case,result);values={}
    for c in limits:
        if c['kind']=='voltage':value=float(result['vm_pu'][c['bus_index']])
        else:value=rows[c['branch_index']][c['terminal']+'_'+('mva' if c['kind']=='mva' else 'current')]
        margin=(value-c['threshold']) if c.get('side')=='lower' else c['threshold']-value
        if c['kind']=='mva':margin/=case.base_mva
        if not math.isfinite(margin):raise ValueError('nonfinite operating limit measurement')
        values[c['key']]=dict(value=value,margin_pu=margin)
    return values


def inventory(family,trace,limits,o):
    samples=[];worst={};crossings=[];previous=None
    for i,p in enumerate(trace['points']):
        values=measure(family.start,p['result'],limits)
        minimum=min(values,key=lambda k:values[k]['margin_pu']) if values else None
        samples.append(dict(point=i,lambda_value=p['lambda_value'],violations=sum(x['margin_pu'] < -o.margin_tolerance_pu for x in values.values()),
            worst_limit=minimum,minimum_margin_pu=None if minimum is None else values[minimum]['margin_pu']))
        for key,x in values.items():
            if key not in worst or x['margin_pu']<worst[key]['margin_pu']:worst[key]=dict(point=i,lambda_value=p['lambda_value'],**x)
            if previous is not None:
                a=previous[key]['margin_pu'];b=x['margin_pu']
                if (a<0)!=(b<0) and max(abs(a),abs(b))>o.margin_tolerance_pu:
                    crossings.append(dict(key=key,before_point=i-1,after_point=i,direction='exit' if b<0 else 'entry'))
        previous=values
    target=None if not trace['completed'] else measure(family.target,trace['final_result'],limits)
    return dict(samples=samples,worst_sampled=worst,detected_crossings=crossings,
        base_violations=[] if not samples else [k for k,x in measure(family.start,trace['points'][0]['result'],limits).items() if x['margin_pu'] < -o.margin_tolerance_pu],
        target=target,target_within_monitored_limits=None if target is None else all(x['margin_pu']>=-o.margin_tolerance_pu for x in target.values()),
        sampled_branch_within_monitored_limits=all(s['violations']==0 for s in samples))


def refine_crossing(family,trace,event,limits,o,arc_o,started,budget,cancelled):
    i=event['before_point'];anchor=family.pack(trace['points'][i]['result'],trace['points'][i]['lambda_value'])
    tangent=np.array(trace['points'][i]['tangent']);right_step=trace['points'][i+1]['arc_step'];key=event['key']
    def endpoint(u,step):
        r=snapshot(family.result(u));value=measure(family.start,r,limits)[key]
        return dict(arc_step=step,lambda_value=float(u[-1]),result=r,**value)
    left=endpoint(anchor,0.);right=endpoint(family.pack(trace['points'][i+1]['result'],trace['points'][i+1]['lambda_value']),right_step)
    history=[];reason=None
    if left['margin_pu']==0:right=left
    elif right['margin_pu']==0:left=right
    for _ in range(o.refinement_iterations):
        if right['arc_step']-left['arc_step']<=o.arc_tolerance:break
        if cancelled is not None and cancelled():reason='cancelled';break
        if time.monotonic()-started>=o.maximum_wall_s:reason='monitor_wall_deadline';break
        if budget[0]>=o.maximum_refinement_solves:reason='monitor_attempt_budget';break
        step=(left['arc_step']+right['arc_step'])/2;budget[0]+=1
        u,iterations,failure=correct(family,anchor+step*tangent,tangent,arc_o)
        if u is None:history.append(dict(arc_step=step,accepted=False,reason=failure));reason='event_corrector_failed';break
        middle=endpoint(u,step);history.append(dict(arc_step=step,accepted=True,margin_pu=middle['margin_pu']))
        if middle['margin_pu']==0:left=middle;right=middle;break
        if (middle['margin_pu']<0)==(left['margin_pu']<0):left=middle
        else:right=middle
    localized=right['arc_step']-left['arc_step']<=o.arc_tolerance
    if not localized and reason is None:reason='event_iteration_budget'
    return dict(**event,left=left,right=right,history=history,localized=localized,reason=reason)


def group_events(events):
    """Order in arc traversal, grouping unresolved/overlapping local intervals."""
    groups=[]
    indexed=sorted(enumerate(events),key=lambda x:(x[1]['before_point'],x[1]['left']['arc_step'],x[1]['key']))
    for index,e in indexed:
        low=e['left']['arc_step'];high=e['right']['arc_step']
        if groups and groups[-1]['before_point']==e['before_point'] and low<=groups[-1]['arc_interval'][1]:
            groups[-1]['event_indices'].append(index);groups[-1]['arc_interval'][1]=max(high,groups[-1]['arc_interval'][1])
        else:groups.append(dict(before_point=e['before_point'],event_indices=[index],arc_interval=[low,high]))
    return groups


def joint_timeline(trace,events):
    """References to stored P/Q and operating-limit events, ordered in arc."""
    timeline=[]
    for kind,source,field in [('p',trace.get('p_control_changes',[]),'generator_index'),('q',trace.get('control_changes',[]),'bus_id')]:
        for index,e in enumerate(source):
            bracket=e['refinement'];left=bracket['left'];right=bracket['right']
            timeline.append(dict(key=f"{kind}:{e[field]}",kind=kind,event_index=index,equipment=e[field],before_point=e['before_point'],after_point=e['after_point'],
                left=dict(arc_step=left['arc_step']),right=dict(arc_step=trace['points'][e['after_point']]['arc_step'] if right is None else right['arc_step']),localized=bracket['localized']))
    for index,e in enumerate(events):
        timeline.append(dict(key='limit:'+e['key'],kind='limit',event_index=index,equipment=e['key'],before_point=e['before_point'],after_point=e['after_point'],
            left=dict(arc_step=e['left']['arc_step']),right=dict(arc_step=e['right']['arc_step']),localized=e['localized']))
    return dict(timeline_events=timeline,timeline_groups=group_events(timeline))


def monitor_trace(document,trace,*,cancelled=None):
    audit_trace(document,trace);started=time.monotonic()
    arc_o=PseudoArcOptions.model_validate(document.get('options',{}));o=EnvelopeOptions.model_validate(document.get('monitor',{}))
    family=make_family(case_from_document(document['start']),case_from_document(document['target']),arc_o)
    family.audit_options=ContinuationOptions(tolerance_pu=arc_o.tolerance_pu,enforce_q_limits=arc_o.enforce_q_limits)
    limits=limits_for(family.start,o);observed=inventory(family,trace,limits,o);events=[];budget=[0];reason='complete'
    if cancelled is not None and cancelled():reason='cancelled'
    elif time.monotonic()-started>=o.maximum_wall_s:reason='monitor_wall_deadline'
    for event in observed['detected_crossings']:
        if reason!='complete':break
        if cancelled is not None and cancelled():reason='cancelled';break
        if time.monotonic()-started>=o.maximum_wall_s:reason='monitor_wall_deadline';break
        if len(events)>=o.maximum_events:reason='monitor_event_budget';break
        refined=refine_crossing(family,trace,event,limits,o,arc_o,started,budget,cancelled);events.append(refined)
        if not refined['localized']:reason=refined['reason'];break
        if cancelled is not None and cancelled():reason='cancelled';break
        if time.monotonic()-started>=o.maximum_wall_s:reason='monitor_wall_deadline';break
    groups=group_events(events)
    first_exit=next((i for i,g in enumerate(groups) if any(events[k]['direction']=='exit' for k in g['event_indices'])),None) if reason=='complete' else None
    return dict(**joint_timeline(trace,events),timeline_complete=bool(reason=='complete' and all(e['localized'] for e in joint_timeline(trace,events)['timeline_events'])),event_groups=groups,first_detected_exit_group=first_exit,schema='power-grid-env.ac-envelope/v1',producer_sha256=package_fingerprint(),options=o.model_dump(mode='json'),limits=limits,**observed,
        events=events,completed=reason=='complete',reason=reason,refinement_solves=budget[0],elapsed_s=time.monotonic()-started,
        exhaustive_event_detection=False,stability_proven=False,protection_actions_simulated=False,
        scope='observed accepted solution branch including possible target overshoot; exact target audited separately; pu currents require terminal voltage bases for conversion to amperes')


def run_envelope(document,out,*,cancelled=None):
    if set(document)-{'start','target','options','monitor','provenance','initial_state'}:raise ValueError('unknown envelope input fields')
    if len(encoded(document))>16000000:raise ValueError('envelope input budget exceeded')
    arc_o=PseudoArcOptions.model_validate(document.get('options',{}));o=EnvelopeOptions.model_validate(document.get('monitor',{}))
    start=case_from_document(document['start']);target=case_from_document(document['target']);limits_for(start,o)
    # Validate compatibility before publishing a directory.
    make_family(start,target,arc_o)
    if document.get('initial_state') is not None:
        from power_grid_env.ac_dispatch import audit_dispatch
        if arc_o.dispatch is None:raise ValueError('audited seed requires distributed controls')
        audit_dispatch(start,arc_o.dispatch,document['initial_state'],branches_required=False)
    out=Path(out);out.mkdir(parents=True,exist_ok=False);(out/'input.json').write_bytes(encoded(document));code=package_fingerprint()
    trace=trace_power_flow(start,target,arc_o.model_dump(mode='json'),cancelled=cancelled,**(dict(initial_state=document['initial_state']) if document.get('initial_state') is not None else {}))
    report=monitor_trace(document,trace,cancelled=cancelled)
    if code!=package_fingerprint() or trace['producer_sha256']!=code:raise ValueError('envelope producer changed during study')
    for name,r in [('trace.json',trace),('envelope.json',report)]:
        raw=encoded(r)
        if len(raw)>64000000:raise ValueError('envelope result budget exceeded')
        (out/name).write_bytes(raw)
    seal(out);return dict(trace=trace,envelope=report)


def audit_envelope(document,trace,r):
    audit_trace(document,trace)
    arc_o=PseudoArcOptions.model_validate(document.get('options',{}));o=EnvelopeOptions.model_validate(document.get('monitor',{}))
    family=make_family(case_from_document(document['start']),case_from_document(document['target']),arc_o)
    family.audit_options=ContinuationOptions(tolerance_pu=arc_o.tolerance_pu,enforce_q_limits=arc_o.enforce_q_limits)
    limits=limits_for(family.start,o);observed=inventory(family,trace,limits,o)
    if r['schema']!='power-grid-env.ac-envelope/v1' or r['producer_sha256']!=package_fingerprint() or r['options']!=o.model_dump(mode='json') or r['limits']!=limits:raise ValueError('envelope producer, options or limits differ')
    if any(r[k] for k in ['exhaustive_event_detection','stability_proven','protection_actions_simulated']):raise ValueError('unsupported envelope proof/action claim')
    if any(r[k]!=v for k,v in observed.items()):raise ValueError('observed operating envelope differs')
    if len(r['events'])>o.maximum_events or len(r['events'])>len(observed['detected_crossings']):raise ValueError('envelope event inventory exceeded')
    timeline=joint_timeline(trace,r['events'])
    if any(r.get(k)!=v for k,v in timeline.items()) or r.get('timeline_complete')!=bool(r['completed'] and all(e['localized'] for e in timeline['timeline_events'])):raise ValueError('joint event timeline differs')
    groups=group_events(r['events'])
    first_exit=next((i for i,g in enumerate(groups) if any(r['events'][k]['direction']=='exit' for k in g['event_indices'])),None) if r['completed'] else None
    if r['event_groups']!=groups or r['first_detected_exit_group']!=first_exit:raise ValueError('event ordering groups differ')
    count=0
    for index,event in enumerate(r['events']):
        expected=observed['detected_crossings'][index]
        if any(event[k]!=v for k,v in expected.items()):raise ValueError('event order or identity differs')
        i=event['before_point'];anchor=family.pack(trace['points'][i]['result'],trace['points'][i]['lambda_value']);tangent=np.array(trace['points'][i]['tangent'])
        lo=0.;hi=trace['points'][i+1]['arc_step'];initial=measure(family.start,trace['points'][i]['result'],limits)[event['key']]['margin_pu'];left_sign=initial<0
        right_initial=measure(family.start,trace['points'][i+1]['result'],limits)[event['key']]['margin_pu']
        if initial==0:hi=lo
        elif right_initial==0:lo=hi
        if len(event['history'])>o.refinement_iterations:raise ValueError('event refinement iteration budget exceeded')
        for j,item in enumerate(event['history']):
            count+=1;mid=(lo+hi)/2
            if lo==hi or item['arc_step']!=mid:raise ValueError('event bisection journal differs')
            if not item['accepted']:
                if j!=len(event['history'])-1 or event['reason']!='event_corrector_failed':raise ValueError('invalid failed refinement')
                break
            margin=item['margin_pu']
            if not math.isfinite(margin):raise ValueError('nonfinite event margin')
            if margin==0:lo=mid;hi=mid
            elif (margin<0)==left_sign:lo=mid
            else:hi=mid
        if event['left']['arc_step']!=lo or event['right']['arc_step']!=hi:raise ValueError('event refinement interval differs')
        for endpoint in [event['left'],event['right']]:
            u=family.pack(endpoint['result'],endpoint['lambda_value']);step=endpoint['arc_step']
            value=measure(family.start,endpoint['result'],limits)[event['key']]
            if endpoint['value']!=value['value'] or endpoint['margin_pu']!=value['margin_pu']:raise ValueError('event terminal measurement differs')
            if not family.valid(u,arc_o) or max(abs(family.evaluate(u)),default=0.)>arc_o.tolerance_pu*2:raise ValueError('event electrical equations failed')
            if not family.audit(endpoint['result'],float(u[-1]))['balance_acceptable']:raise ValueError('event balance failed')
            if abs(tangent@(u-anchor-step*tangent))>arc_o.tolerance_pu*2:raise ValueError('event predictor hyperplane differs')
        a=event['left']['margin_pu'];b=event['right']['margin_pu']
        if lo!=hi and ((a<0)!=left_sign or (b<0)==left_sign):raise ValueError('event endpoints do not bracket the crossing')
        if lo==hi and (a!=0 or b!=0):raise ValueError('zero-width event is not on the limit')
        if event['reason'] not in {None,'cancelled','monitor_wall_deadline','monitor_attempt_budget','event_corrector_failed','event_iteration_budget'}:raise ValueError('invalid event termination reason')
        localized=hi-lo<=o.arc_tolerance
        if event['localized']!=localized or (localized and event['reason'] is not None):raise ValueError('event localization conclusion differs')
        if not localized and index!=len(r['events'])-1:raise ValueError('unlocalized event must end the monitor')
    if count!=r['refinement_solves'] or count>o.maximum_refinement_solves:raise ValueError('monitor solve budget differs')
    allowed={'complete','cancelled','monitor_wall_deadline','monitor_attempt_budget','monitor_event_budget','event_corrector_failed','event_iteration_budget'}
    if r['reason'] not in allowed or r['completed']!=(r['reason']=='complete'):raise ValueError('invalid envelope completion claim')
    if r['completed'] and (len(r['events'])!=len(observed['detected_crossings']) or any(not e['localized'] for e in r['events'])):raise ValueError('missing localized limit crossings')
    if r['reason']=='monitor_event_budget' and (len(r['events'])!=o.maximum_events or len(r['events'])==len(observed['detected_crossings'])):raise ValueError('event exhaustion differs')
    if r['reason']=='monitor_attempt_budget' and count!=o.maximum_refinement_solves:raise ValueError('solve exhaustion differs')
    if r['reason']=='event_iteration_budget' and (not r['events'] or len(r['events'][-1]['history'])!=o.refinement_iterations):raise ValueError('iteration exhaustion differs')
    if r['reason'] in {'event_corrector_failed','event_iteration_budget'} and (not r['events'] or r['events'][-1]['localized'] or r['events'][-1]['reason']!=r['reason']):raise ValueError('refinement failure differs')
    return dict(verified=True,completed=bool(trace['completed'] and r['completed']),monitor_completed=r['completed'],target_reached=trace['completed'],
        target_within_monitored_limits=r['target_within_monitored_limits'],monitored_limits=len(limits),observed_events=len(r['events']),p_control_changes=len(trace.get('p_control_changes',[])),q_control_changes=len(trace.get('control_changes',[])),timeline_complete=r['timeline_complete'],stability_proven=False,
        scope='recomputed stored branch, terminal measurements, event brackets and sampled envelope; no Newton/time replay, source authentication or exhaustive excursion proof')


def verify_envelope(directory):
    directory=Path(directory);identity=verify(directory)
    return dict(manifest_sha256=identity,**audit_envelope(read_json(directory/'input.json'),read_json(directory/'trace.json'),read_json(directory/'envelope.json')))
