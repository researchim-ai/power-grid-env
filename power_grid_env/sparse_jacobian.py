"""Stream exact analytic column blocks into CSC, without a dense state square.

The final CSC may itself have quadratic nnz after network reduction. No zeros
are inferred from topology, no nonzero thresholding or FD fallback is used.
"""
import numpy as np
from scipy.sparse import csc_matrix


def assemble(callback,time,state,*,block_columns=32):
    state=np.asarray(state);n=len(state)
    if state.ndim!=1 or not np.isrealobj(state) or not np.isfinite(state).all():raise ValueError('invalid sparse Jacobian state')
    if type(block_columns) is not int or block_columns<1:raise ValueError('positive Jacobian block width required')
    context=callback.jacobian_context(time,state)
    data=[];indices=[];indptr=[0]
    for first in range(0,n,block_columns):
        cols=np.arange(first,min(n,first+block_columns))
        block=callback.jacobian_columns(time,state,cols,context)
        if block.shape!=(n,len(cols)) or not np.isrealobj(block) or not np.isfinite(block).all():raise ValueError('invalid analytic Jacobian column block')
        for j in range(len(cols)):
            rows=np.flatnonzero(block[:,j]);values=block[rows,j]
            indices.append(rows.astype(np.int32));data.append(values);indptr.append(indptr[-1]+len(rows))
    values=np.concatenate(data) if data else np.empty(0)
    rows=np.concatenate(indices) if indices else np.empty(0,dtype=np.int32)
    result=csc_matrix((values,rows,np.asarray(indptr,dtype=np.int64)),shape=(n,n),copy=False)
    return result
