"""Exact opt-in streamed CSC for supported analytic callbacks."""
import numpy as np
from scipy.sparse import csc_matrix


def direct_supported(callback):
    return (getattr(callback,'jacobian_network_response',None)=='direct_solve'
            and callable(getattr(callback,'jacobian_columns',None))
            and callable(getattr(callback,'jacobian_context',None)))


def configure(callback,storage):
    if storage not in ('dense','csc'):raise ValueError('unknown Jacobian storage')
    if callback is None:return None
    if storage=='dense':return callback
    streamed=hasattr(callback,'jacobian_columns') and hasattr(callback,'jacobian_context')
    def evaluate(time,state):
        if streamed:
            from power_grid_env.sparse_jacobian import assemble
            return assemble(callback,time,state)
        dense=callback(time,state)
        if dense.shape!=(len(state),len(state)) or not np.isrealobj(dense) or not np.isfinite(dense).all():raise ValueError('invalid analytic matrix for CSC delivery')
        result=csc_matrix(dense);result.sort_indices()
        return result
    evaluate.jacobian_kind=getattr(callback,'jacobian_kind','analytic_motor_network_reduction')
    evaluate.jacobian_network_response=getattr(callback,'jacobian_network_response','cached_ports')
    evaluate.jacobian_storage='csc'
    evaluate.jacobian_assembly='column_blocks' if streamed else 'dense_conversion'
    return evaluate
