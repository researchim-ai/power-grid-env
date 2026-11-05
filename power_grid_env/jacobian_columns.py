"""Bounded analytic tangent seeds; no full state-square identity required."""
import numpy as np


def seeds(dimension,columns):
    columns=np.asarray(columns)
    if columns.ndim!=1 or columns.dtype.kind not in 'iu' or len(np.unique(columns))!=len(columns) or np.any(columns<0) or np.any(columns>=dimension):raise ValueError('invalid Jacobian column inventory')
    unit=np.zeros((dimension,len(columns)))
    unit[columns,np.arange(len(columns))]=1.
    return columns,unit


def tangent_seeds(dimension,values):
    values=np.asarray(values)
    if values.ndim!=2 or values.shape[0]!=dimension or not np.isrealobj(values) or not np.isfinite(values).all():raise ValueError('invalid real tangent directions')
    return values


def attach(callback,model,evaluate,actions=None):
    callback.jacobian_context=lambda time,state:model.electrical(state)
    callback.jacobian_columns=evaluate
    if actions is not None:
        callback.jacobian_actions=actions
        callback.dae_actions=actions
    callback.jacobian_network_response=getattr(model.settings,'jacobian_network','cached_ports')
    return callback
