"""Sparse ordered affine reset maps and exact local pushforwards.

Reset rows read the state produced by preceding operations. Reordering maps is
never assumed harmless. Hidden clock/context sensitivities are handled by the
complete-study numerical driver, not silently represented as physical states.
"""
from dataclasses import dataclass,field
import math
import numpy as np


@dataclass(frozen=True)
class ResetRow:
    row:int
    value:float
    weights:dict[int,float]=field(default_factory=dict)
    parameter_weights:dict[str,float]=field(default_factory=dict)

    def __post_init__(self):
        if type(self.row) is not int or self.row<0 or any(type(k) is not int or k<0 for k in self.weights):raise ValueError('nonnegative reset row/column indices required')
        if not all(math.isfinite(v) for v in [self.value,*self.weights.values(),*self.parameter_weights.values()]):raise ValueError('finite reset coefficients required')
        if any(not isinstance(k,str) or not k for k in self.parameter_weights):raise ValueError('named parameter directions required')


def apply_resets(state,operations):
    x=np.asarray(state)
    if x.ndim!=1 or not np.isrealobj(x) or not np.isfinite(x).all():raise ValueError('finite real reset state required')
    x=x.astype(float,copy=True)
    for op in operations:
        if op.row>=len(x) or any(k>=len(x) for k in op.weights):raise ValueError('reset index outside state')
        x[op.row]=op.value+sum(weight*x[k] for k,weight in op.weights.items())
    if not np.isfinite(x).all():raise ValueError('nonfinite reset state')
    return x


def reset_product(direction,operations,parameter_direction=None):
    v=np.asarray(direction)
    if v.ndim not in (1,2) or not np.isrealobj(v) or not np.isfinite(v).all():raise ValueError('finite real reset directions required')
    v=v.astype(float,copy=True);parameters=parameter_direction or {}
    for value in parameters.values():
        a=np.asarray(value)
        if not np.isrealobj(a) or not np.isfinite(a).all() or (a.shape!=() and (v.ndim!=2 or a.shape!=(v.shape[1],))):raise ValueError('finite real parameter direction of matching width required')
    for op in operations:
        if op.row>=len(v) or any(k>=len(v) for k in op.weights):raise ValueError('reset index outside direction')
        value=sum(weight*v[k] for k,weight in op.weights.items())
        value=value+sum(weight*np.asarray(parameters.get(k,0.)) for k,weight in op.parameter_weights.items())
        v[op.row]=value
    if not np.isfinite(v).all():raise ValueError('nonfinite reset product')
    return v


def event_reset_rows(model,event):
    """Exact state assignments for supported actual controller/automation logs.

    Unlisted reset families raise rather than returning an invented identity.
    Context-only events must be treated separately by a hybrid-flow derivative.
    """
    kind=event['kind'];id_=event.get('equipment_id')
    if kind=='controller_limit_enter':
        for address,(i,group,lo,hi) in model.controller_boundaries.entries.items():
            if model.generators[i].id==id_ and group==event['controller']:
                side=event['bound']
                if side not in {'lower','upper'}:raise ValueError('invalid controller bound')
                return [ResetRow(address,lo if side=='lower' else hi,parameter_weights={id_+'.'+group+'.'+side:1.})]
    elif kind in {'uel_lag_reset','esst1a_regulator_reset'}:
        bank=model.underexcitation_limiters if kind=='uel_lag_reset' else model.esst1a
        for a,p in bank.entries.values():
            if p.generator_id==id_:
                side=event['bound']
                if side not in {'lower','upper'}:raise ValueError('invalid limiter reset bound')
                value=(0. if side=='lower' else p.MEL_MAX) if kind=='uel_lag_reset' else (p.V_AMIN if side=='lower' else p.V_AMAX)
                return [ResetRow(a if kind=='uel_lag_reset' else a+3,value,parameter_weights={id_+'.'+kind+'.'+side:1.} if kind!='uel_lag_reset' or side=='upper' else {})]
    elif kind=='oel2c_transition':
        transition=event['transition'];label,_,side=transition.partition('_')
        if label in {'timer','reference'} and side in {'lower','upper'}:
            for a,p,k in model.oel2c.entries.values():
                if p.generator_id==id_:
                    row,lo,hi=(a+1,p.tMin,p.tMax) if label=='timer' else (a+2,p.ILimPu,p.IInstPu)
                    return [ResetRow(row,lo if side=='lower' else hi,parameter_weights={id_+'.OEL2C.'+transition:1.})]
        raise ValueError('OEL2C clock/branch transition is a context map; use full-study derivative')
    elif kind in {'network_relay_trip','relay_reset','transfer_started','ats_reset'}:
        bank=model.automation
        if bank is None:raise ValueError('automation bank required')
        transfer=kind in {'transfer_started','ats_reset'};items=bank.transfers if transfer else bank.relays
        for i,p in enumerate(items):
            if p.id==id_:
                address=bank.offset+(len(bank.relays) if transfer else 0)+i
                return [ResetRow(address,0. if kind.endswith('reset') else 1.)]
    raise ValueError('unsupported or context-only reset event: '+kind)
