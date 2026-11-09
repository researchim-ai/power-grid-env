"""Sparse stamps and exact polar derivatives for the balanced AC profile."""
import numpy as np
from scipy.sparse import coo_matrix


def admittance(case,index):
    from power_grid_env.network_opf import _branch_quad
    rows=[];columns=[];values=[]
    for line in case.branches:
        if not line.status:continue
        f,t=index[line.fbus],index[line.tbus]
        rows.extend([f,f,t,t]);columns.extend([f,t,f,t]);values.extend(_branch_quad(line))
    for b in case.buses:
        rows.append(index[b.id]);columns.append(index[b.id]);values.append(complex(b.gs,b.bs))
    y=coo_matrix((values,(rows,columns)),shape=(len(index),len(index)),dtype=complex).tocsc()
    y.sum_duplicates();y.eliminate_zeros();return y


def derivatives(vm,theta,y):
    """Four sparse dP/dtheta, dQ/dtheta, dP/dVm, dQ/dVm blocks.

    Elementwise nonzero formulas avoid sparse diagonal matrix products and
    preserve arbitrary bus ordering, parallel branches, taps and phase shifts.
    """
    v=vm*np.exp(1j*theta);current=y@v;unit=v/vm;entries=y.tocoo();n=len(vm);diagonal=np.arange(n)
    rows=np.r_[entries.row,diagonal];cols=np.r_[entries.col,diagonal]
    a=-1j*v[entries.row]*np.conj(entries.data*v[entries.col])
    b=v[entries.row]*np.conj(entries.data*unit[entries.col])
    da=coo_matrix((np.r_[a,1j*v*np.conj(current)],(rows,cols)),shape=y.shape).tocsc()
    dv=coo_matrix((np.r_[b,unit*np.conj(current)],(rows,cols)),shape=y.shape).tocsc()
    da.eliminate_zeros();dv.eliminate_zeros()
    return da.real,da.imag,dv.real,dv.imag


def stored_bytes(matrix):
    return matrix.data.nbytes+matrix.indices.nbytes+matrix.indptr.nbytes
