"""Exact continuous J times vectors, without assembling a Jacobian.

The model's current discrete mode is used for this immediate call. No operator
is retained across topology, controller branch or relay-context changes. This
is not an integrator, DAE solver, finite difference or event saltation map.
"""
import numpy as np


def product(callback,time,state,directions,*,block_columns=8):
    """Return J(t,x) @ v or J(t,x) @ V using bounded analytic actions.

    Missing directional callbacks fail explicitly; a dense/FD fallback would
    defeat the memory contract. Input state/directions are never modified.
    """
    state=np.asarray(state);directions=np.asarray(directions)
    if state.ndim!=1 or not len(state) or state.dtype.kind not in 'fiu' or not np.isfinite(state).all():raise ValueError('finite real state vector required')
    if not np.isscalar(time) or not np.isrealobj(time) or not np.isfinite(time):raise ValueError('finite real tangent time required')
    if type(block_columns) is not int or not 1<=block_columns<=8:raise ValueError('tangent block width must be in 1..8')
    vector=directions.ndim==1
    if vector:directions=directions[:,None]
    if directions.ndim!=2 or directions.shape[0]!=len(state) or not directions.shape[1] or directions.dtype.kind not in 'fiu' or not np.isfinite(directions).all():raise ValueError('finite real tangent vectors required')
    if not callable(getattr(callback,'jacobian_actions',None)) or not callable(getattr(callback,'jacobian_context',None)):raise ValueError('analytic directional callback required; no dense or FD fallback')
    context=callback.jacobian_context(time,state)
    result=np.empty(directions.shape,dtype=float)
    for first in range(0,directions.shape[1],block_columns):
        last=min(first+block_columns,directions.shape[1])
        block=callback.jacobian_actions(time,state,directions[:,first:last],context)
        if block.shape!=(len(state),last-first) or not np.isrealobj(block) or not np.isfinite(block).all():raise ValueError('invalid analytic directional result')
        result[:,first:last]=block
    return result[:,0] if vector else result
