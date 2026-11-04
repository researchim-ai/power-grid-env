"""Fail-closed audit of causal native MNLEX2 reset operands.

The ULP score describes local operand separation only. It is neither a bound
on network uncertainty nor a relaxation of the event-time acceptance limit.
"""
from collections import Counter
from collections.abc import Mapping
import math
from benchmarks.native_uel_roots import ATOMIC_INDICATORS,POLICIES

REASONS={'own_state_event':'native_root','input_discontinuity':None,
         'own_time_event':'native_time','own_step_event':'integrator_step',
         'own_post_step_event':'native_root'}
FIELDS=('raw_state_pu','native_lag_input_pu','target_pu',
        'source_guard_numerator_pu','native_ode_rate_pu_per_s')
LOCAL_ULPS=32.


def number(value):
    try:
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError()
        return float(value)
    except (ValueError,OverflowError,TypeError) as exc:
        raise ValueError('finite numerical guard evidence required') from exc


def snapshot(value,p):
    if not isinstance(value,Mapping) or value.get('source_guard_reconstructed_from_native_operands') is not True:
        raise ValueError('native source guard operand snapshot required')
    if not set(FIELDS)<=value.keys():raise ValueError('incomplete native guard snapshot')
    x={key:number(value[key]) for key in FIELDS}
    inputs=value.get('native_inputs')
    if not isinstance(inputs,(list,tuple)) or len(inputs)!=3:raise ValueError('three native UEL inputs required')
    x['native_inputs']=[number(v) for v in inputs]
    if x['native_inputs'][2]<0:raise ValueError('negative native voltage magnitude')
    raw=x['raw_state_pu'];target=p.K_M*x['native_lag_input_pu'];residual=target-raw
    if not math.isfinite(target) or x['target_pu']!=target or x['source_guard_numerator_pu']!=residual:
        raise ValueError('source guard operands are inconsistent')
    for key,expected in [('strict_lower_condition',raw<0. and residual>0.),('strict_upper_condition',raw>p.MEL_MAX and residual<0.)]:
        if type(value.get(key)) is not bool or value[key]!=expected:raise ValueError('source guard condition is inconsistent')
        x[key]=expected
    # Allow rounding from the two algebraic arrangements seen in the pinned
    # generated C: (K*u-state)/T and (K/T)*u-(1/T)*state. This is a local
    # arithmetic consistency check, not an error bound for the whole model.
    terms=(p.K_M/p.T_M*x['native_lag_input_pu'],raw/p.T_M)
    if not all(math.isfinite(t) for t in terms):raise ValueError('nonfinite native guard scale')
    budget=16*sum(math.ulp(t) for t in terms)
    error=abs(x['native_ode_rate_pu_per_s']-residual/p.T_M)
    if not math.isfinite(error) or error>budget:raise ValueError('native derivative disagrees with source guard operands')
    x.update(local_ode_roundoff_budget=budget,native_ode_form_error=error,
             local_operand_separation_ulps=abs(residual)/(math.ulp(target)+math.ulp(raw)))
    return x


def audit(spec,report):
    if report.get('completed') is not True or report.get('native_UEL_event_policy')!='causal':raise ValueError('complete causal native reference required')
    if report.get('native_MNLEX2_in_feedback') is not True:raise ValueError('native UEL feedback required')
    if not isinstance(report.get('event_log'),list):raise ValueError('native reset event log required')
    root_policy=report.get('native_UEL_root_policy','compound_and_atoms')
    if root_policy not in POLICIES:raise ValueError('unknown native UEL root policy')
    passports={p.generator_id:p for p in spec.coupled.underexcitation_limiters}
    for gid,p in passports.items():
        if p.event_detection=='conditioned_1e-8':
            metadata=report.get('native_UEL_initialization',{}).get(gid,{})
            if metadata.get('event_detection')!=p.event_detection or metadata.get('event_rate_floor_per_s')!=1e-8:raise ValueError('conditioned UEL event policy evidence required')
    rows=[];counts=Counter();last={}
    for event in report['event_log']:
        if not isinstance(event,Mapping) or not isinstance(event.get('kind'),str):raise ValueError('typed native event record required')
        if event['kind']=='uel_lag_reset':raise ValueError('primary UEL reset in native reference')
        if event['kind']!='native_mnlex2_lag_reset':continue
        if not {'equipment_id','time_s','guard_before_commands','guard_before_update','guard_after_update','raw_state_before_pu','raw_state_after_pu','bound'}<=event.keys():raise ValueError('incomplete native reset evidence')
        gid=event['equipment_id']
        if gid not in passports:raise ValueError('reset for undeclared native UEL')
        p=passports[gid];t=number(event['time_s'])
        if not 0<=t<=spec.coupled.duration_s or t<last.get(gid,0.):raise ValueError('native reset time outside ordered horizon')
        last[gid]=t
        before=snapshot(event['guard_before_commands'],p);active=snapshot(event['guard_before_update'],p);after=snapshot(event['guard_after_update'],p)
        raw_before=number(event['raw_state_before_pu']);raw_after=number(event['raw_state_after_pu']);bound=event['bound']
        if bound not in ('lower','upper'):raise ValueError('unknown native reset bound')
        if before['raw_state_pu']!=raw_before or active['raw_state_pu']!=raw_before or after['raw_state_pu']!=raw_after:
            raise ValueError('reset side differs from native guard snapshot')
        if not active['strict_'+bound+'_condition'] or raw_after!=(0. if bound=='lower' else p.MEL_MAX):raise ValueError('reset is not justified by original source condition')
        reasons=event.get('event_iteration_reasons')
        if not isinstance(reasons,list) or not reasons or any(not isinstance(r,str) or r not in REASONS for r in reasons) or len(set(reasons))!=len(reasons):raise ValueError('own native event causes required')
        if root_policy=='continuous_atoms':
            indices=event.get('root_indicator_indices');names=event.get('root_indicator_names')
            if not isinstance(indices,list) or not isinstance(names,list) or any(type(i) is not int or not 0<=i<len(ATOMIC_INDICATORS) for i in indices) or len(set(indices))!=len(indices):raise ValueError('local continuous root provenance required')
            if names!=[ATOMIC_INDICATORS[i] for i in indices] or bool(indices)!=('own_state_event' in reasons):raise ValueError('local root provenance disagrees with event cause')
        changed=before['native_inputs']!=active['native_inputs']
        if ('input_discontinuity' in reasons)!=changed:raise ValueError('input discontinuity evidence disagrees with event cause')
        trigger=event.get('trigger',{})
        if not isinstance(trigger,Mapping):raise ValueError('native event trigger map required')
        for reason in reasons:
            key=REASONS[reason]
            if key is not None and trigger.get(key) is not True:raise ValueError('native event trigger is missing')
        # MNLEX2 reinitializes only the raw lag beyond a bound. Its clipped
        # output and derivative-lag memory do not jump during this reinit.
        if after['native_inputs']!=active['native_inputs'] or after['target_pu']!=active['target_pu']:raise ValueError('native reset changes unrelated input or feedback')
        counts.update(reasons)
        rows.append(dict(generator_id=gid,time_s=t,bound=bound,event_iteration_reasons=list(reasons),
            source_guard_numerator_pu=active['source_guard_numerator_pu'],
            local_operand_separation_ulps=active['local_operand_separation_ulps'],
            guard_sign_within_32_operand_ulps=active['local_operand_separation_ulps']<=LOCAL_ULPS,
            native_ode_form_error=active['native_ode_form_error'],local_ode_roundoff_budget=active['local_ode_roundoff_budget']))
    return dict(passed=True,native_UEL_root_policy=root_policy,resets=len(rows),event_reasons=dict(counts),rows=rows,
        low_local_separation_resets=sum(r['guard_sign_within_32_operand_ulps'] for r in rows),
        local_resolution_threshold_ulps=LOCAL_ULPS,conditioning_is_diagnostic_only=True,
        event_times_certified_by_this_audit=False,industrial_profile_qualified=False)
