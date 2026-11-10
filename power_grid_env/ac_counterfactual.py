"""Finite signed load scenarios with nonlinear branch replay and local predictions.

A prediction never replaces the independently audited nonlinear endpoint.
Topology, schedules and equipment limits remain fixed; monitoring does not trip.
"""
from dataclasses import replace
from pathlib import Path
import copy,math
import numpy as np
from pydantic import Field,model_validator
from power_grid_env.ac_sensitivity import Strict,LoadChange,Output,sensitivities
from power_grid_env.ac_dispatch import solve_dispatch,audit_dispatch
from power_grid_env.ac_pseudo_arc import PseudoArcOptions
from power_grid_env.ac_envelope import EnvelopeOptions,run_envelope,verify_envelope,terminal_values
from power_grid_env.balanced_ac import case_document,case_from_document,branch_results
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


class CounterfactualInput(Strict):
    case:dict
    state:dict|None=None
    changes:list[LoadChange]=Field(min_length=1,max_length=2048)
    amount:float=Field(default=1.,ge=-1e6,le=1e6)
    outputs:list[Output]=Field(min_length=1,max_length=32)
    options:PseudoArcOptions
    monitor:EnvelopeOptions=Field(default_factory=EnvelopeOptions)
    switch_margin_pu:float=Field(default=1e-7,ge=1e-10,le=.01)
    provenance:dict=Field(default_factory=dict)

    @model_validator(mode='after')
    def policy(self):
        if self.options.dispatch is None or not self.options.enforce_q_limits:raise ValueError('explicit distributed P/Q policy required')
        if len({x.bus_id for x in self.changes})!=len(self.changes):raise ValueError('duplicate changed bus')
        if len({x.name for x in self.outputs})!=len(self.outputs):raise ValueError('duplicate output name')
        return self


def cases(d):
    before=case_from_document(d.case);idx={b.id:i for i,b in enumerate(before.buses)}
    if any(x.bus_id not in idx for x in d.changes):raise ValueError('unknown changed bus')
    for o in d.outputs:
        if o.bus_id is not None and o.bus_id not in idx:raise ValueError('unknown output bus')
        if o.generator_index is not None and o.generator_index>=len(before.generators):raise ValueError('unknown output generator')
        if o.branch_index is not None and o.branch_index>=len(before.branches):raise ValueError('unknown output branch')
    changes={x.bus_id:x for x in d.changes}
    after=replace(before,buses=[replace(b,pd=b.pd+d.amount*changes[b.id].dp_mw,qd=b.qd+d.amount*changes[b.id].dq_mvar) if b.id in changes else b for b in before.buses]);case_document(after)
    return before,after


def measurements(case,state,outputs):
    idx={b.id:i for i,b in enumerate(case.buses)};branches=None;terminals=None;values={}
    for o in outputs:
        if o.kind=='voltage':value=state['vm_pu'][idx[o.bus_id]];units='pu'
        elif o.kind=='angle':value=math.radians(state['va_deg'][idx[o.bus_id]]);units='rad'
        elif o.kind=='pg':value=state['pg_mw'][o.generator_index];units='MW'
        elif o.kind=='delta':value=state['delta_mw'];units='MW'
        else:
            if branches is None:
                branches=branch_results(case,np.array(state['vm_pu']),np.deg2rad(state['va_deg']));terminals=terminal_values(case,state)
            if o.kind=='branch_current':value=terminals[o.branch_index][o.terminal+'_current'];units='pu-current'
            else:
                key={'branch_p':'p_'+o.terminal+'_mw','branch_q':'q_'+o.terminal+'_mvar','branch_mva':'s_'+o.terminal+'_mva'}[o.kind];value=branches[o.branch_index][key];units={'branch_p':'MW','branch_q':'Mvar','branch_mva':'MVA'}[o.kind]
        values[o.name]=dict(value=float(value),units=units)
    return values


def linear_prediction(d,base):
    doc=dict(case=d.case,state=base,dispatch=d.options.dispatch.model_dump(mode='json'),directions=[dict(name='scenario',loads=[x.model_dump(mode='json') for x in d.changes])],outputs=[x.model_dump(mode='json',exclude_none=True) for x in d.outputs],switch_margin_pu=d.switch_margin_pu)
    try:r=sensitivities(doc)
    except ValueError as exc:
        if str(exc)!='magnitude output is not differentiable at zero':raise
        return dict(available=False,reason='zero_magnitude_not_differentiable',result=None)
    return dict(available=r['completed'],reason=r['reason'],result=r)


def native_input(d,base):
    before,after=cases(d)
    return dict(start=case_document(before),target=case_document(after),initial_state=base,options=d.options.model_dump(mode='json'),monitor=d.monitor.model_dump(mode='json'),provenance=dict(source=d.provenance,policy='finite declared signed Pd/Qd change; unchanged topology, Pg schedules, equipment bounds; audited branch seed'))


def events_in(study):
    out=[]
    for kind,source,key in [('p',study['trace']['p_control_changes'],'generator_index'),('q',study['trace']['control_changes'],'bus_id'),('limit',study['envelope']['events'],'key')]:
        for i,e in enumerate(source):
            bracket=e if kind=='limit' else e['refinement'];right=bracket['right'];mid=None if right is None else (bracket['left']['lambda_value']+right['lambda_value'])/2
            out.append(dict(kind=kind,event_index=i,equipment=e[key],lambda_mid=mid,localized=bracket['localized'],within_requested_interval=None if mid is None else bool(0<=mid<=1)))
    return out


def report(d,base,linear,study):
    before,after=cases(d);common=dict(schema='power-grid-env.ac-counterfactual/v1',producer_sha256=package_fingerprint(),amount=d.amount,global_target_absence_proven=False,stability_proven=False,operating_recommendation=False,exhaustive_event_detection=False)
    if not base['completed']:return dict(common,completed=False,requested_change_reached=False,reason='base_'+base['reason'],linear_prediction_available=False,linear_reason=None,outputs=[],target_audit=None,target_within_monitored_limits=None,active_set_changed=None,linear_applicability='unavailable',events=[])
    initial=base['state'];a=measurements(before,initial,d.outputs);final=None if study is None or not study['trace']['completed'] else study['trace']['final_result'];b={} if final is None else measurements(after,final,d.outputs)
    events=[] if study is None else events_in(study);changed=None if final is None else final['p_modes']!=initial['p_modes'] or final['q_modes']!=initial['q_modes'];observed_switch=any(e['kind'] in ['p','q'] and e['within_requested_interval'] is not False for e in events)
    derivative={} if not linear['available'] else linear['result']['forward'][0]['outputs'];rows=[]
    for o in d.outputs:
        predicted=None if not linear['available'] else a[o.name]['value']+d.amount*derivative[o.name]
        actual=None if final is None else b[o.name]['value']
        rows.append(dict(name=o.name,units=a[o.name]['units'],before=a[o.name]['value'],linear_after=predicted,actual_after=actual,actual_change=None if actual is None else actual-a[o.name]['value'],linear_error=None if actual is None or predicted is None else actual-predicted))
    audit=None if final is None else audit_dispatch(after,d.options.dispatch,final,branches_required=False)
    reason='cancelled' if study is None else 'requested_change_reached' if final is not None else 'sampled_turn_before_target' if study['trace']['reason']=='sampled_turn_followed' else study['trace']['reason']
    completed=bool(final is not None and study['envelope']['completed'])
    if final is not None and not study['envelope']['completed']:reason=study['envelope']['reason']
    return dict(common,completed=completed,requested_change_reached=final is not None,reason=reason,linear_prediction_available=linear['available'],linear_reason=linear['reason'],outputs=rows,target_audit=audit,target_within_monitored_limits=None if study is None else study['envelope']['target_within_monitored_limits'],active_set_changed=changed,linear_applicability='unavailable' if not linear['available'] else 'observed_control_switch' if observed_switch or changed else 'no_control_switch_observed',events=events)


def run_counterfactual(document,out,*,cancelled=None):
    if len(encoded(document))>16_000_000:raise ValueError('counterfactual input budget exceeded')
    d=CounterfactualInput.model_validate(document);before,_=cases(d);producer=package_fingerprint()
    if d.state is None:
        solved=solve_dispatch(before,d.options.dispatch,cancelled=cancelled);base=dict(completed=solved['completed'],reason=solved['reason'],state=solved['result'],audit=solved['audit'])
    else:
        state=copy.deepcopy(d.state);audit=audit_dispatch(before,d.options.dispatch,state,branches_required=False);base=dict(completed=True,reason='audited_supplied_state',state=state,audit=audit)
    linear=None;study=None
    out=Path(out);out.mkdir(parents=True,exist_ok=False);(out/'input.json').write_bytes(encoded(document));(out/'base.json').write_bytes(encoded(base))
    if base['completed']:
        linear=linear_prediction(d,base['state']);(out/'linear.json').write_bytes(encoded(linear))
        if cancelled is None or not cancelled():study=run_envelope(native_input(d,base['state']),out/'study',cancelled=cancelled)
    if producer!=package_fingerprint():raise ValueError('counterfactual producer changed')
    result=report(d,base,linear,study);(out/'result.json').write_bytes(encoded(result));seal(out);return result


def verify_counterfactual(out):
    out=Path(out);identity=verify(out);document=read_json(out/'input.json')
    if len(encoded(document))>16_000_000:raise ValueError('counterfactual input budget exceeded')
    d=CounterfactualInput.model_validate(document);before,_=cases(d);base=read_json(out/'base.json');linear=None;study=None
    if base['completed']:
        if base['reason']!=('converged' if d.state is None else 'audited_supplied_state'):raise ValueError('base origin differs')
        audit=audit_dispatch(before,d.options.dispatch,base['state'],branches_required=False)
        if audit!=base['audit'] or d.state is not None and encoded(d.state)!=encoded(base['state']):raise ValueError('counterfactual base differs')
        linear=linear_prediction(d,base['state'])
        if encoded(read_json(out/'linear.json'))!=encoded(linear):raise ValueError('linear prediction differs')
        if (out/'study').exists():
            verify_envelope(out/'study')
            if encoded(read_json(out/'study/input.json'))!=encoded(native_input(d,base['state'])):raise ValueError('finite scenario input differs')
            study=dict(trace=read_json(out/'study/trace.json'),envelope=read_json(out/'study/envelope.json'))
    elif d.state is not None or base['state'] is not None or base['audit'] is not None or base['reason'] not in {'cancelled','wall_deadline','producer_changed','islanded','iteration_budget','dispatch_line_search_failed','state_domain','singular_dispatch_jacobian','participating_p_limits_active'} or (out/'study').exists() or (out/'linear.json').exists():raise ValueError('invalid failed base record')
    expected=report(d,base,linear,study)
    if encoded(read_json(out/'result.json'))!=encoded(expected):raise ValueError('counterfactual conclusion differs')
    return dict(verified=True,completed=expected['completed'],requested_change_reached=expected['requested_change_reached'],manifest_sha256=identity,global_target_absence_proven=False)
