"""Exact Norton-port response with bounded temporary right-hand-side blocks.

Retains only requested port rows. A reduced network may be dense: no entry is
thresholded, no topology sparsity is assumed and no derivative is approximated.
"""
import numpy as np


def directional_response(model,buses,admittances,output_buses,directions,*,block_columns=8):
    """Solve Y dV = sum(y_port dE_port), without a port-square reduction.

    Repeated buses are accumulated, including motor/generator co-location.
    Uses the current topology's LU on every call; no sensitivity cache lives
    beyond that topology. The temporary network RHS has at most eight columns.
    """
    buses=np.asarray(buses);output_buses=np.asarray(output_buses)
    admittances=np.asarray(admittances);directions=np.asarray(directions)
    n=len(model.bus_names)
    if type(block_columns) is not int or block_columns<1 or block_columns>8:raise ValueError('network tangent block width must be in 1..8')
    if buses.ndim!=1 or buses.dtype.kind not in 'iu' or output_buses.ndim!=1 or output_buses.dtype.kind not in 'iu':raise ValueError('integer tangent bus inventory required')
    if admittances.shape!=buses.shape or directions.ndim!=2 or directions.shape[0]!=len(buses):raise ValueError('invalid network tangent shape')
    if np.any(buses<0) or np.any(buses>=n) or np.any(output_buses<0) or np.any(output_buses>=n):raise ValueError('invalid network tangent bus')
    if not np.isfinite(admittances).all() or not np.isfinite(directions).all():raise ValueError('nonfinite network tangent')
    result=np.empty((len(output_buses),directions.shape[1]),dtype=complex)
    for first in range(0,directions.shape[1],block_columns):
        last=min(first+block_columns,directions.shape[1]);rhs=np.zeros((n,last-first),dtype=complex)
        np.add.at(rhs,buses,admittances[:,None]*directions[:,first:last])
        solved=model.lu.solve(rhs)
        if not np.isfinite(solved).all():raise ValueError('nonfinite network tangent solution')
        result[:,first:last]=solved[output_buses]
    return result


def port_response(model,buses,admittances,output_buses,*,block_columns=8):
    buses=np.asarray(buses,dtype=int);admittances=np.asarray(admittances,dtype=complex);output_buses=np.asarray(output_buses,dtype=int)
    n=len(model.bus_names)
    if type(block_columns) is not int or block_columns<1:raise ValueError('positive sensitivity block width required')
    if buses.ndim!=1 or admittances.shape!=buses.shape or output_buses.ndim!=1:raise ValueError('invalid sensitivity port shape')
    if not np.isfinite(admittances).all() or np.any(buses<0) or np.any(buses>=n) or np.any(output_buses<0) or np.any(output_buses>=n):raise ValueError('invalid sensitivity port')
    result=np.empty((len(output_buses),len(buses)),dtype=complex)
    for start in range(0,len(buses),block_columns):
        stop=min(start+block_columns,len(buses));rhs=np.zeros((n,stop-start),dtype=complex)
        rhs[buses[start:stop],np.arange(stop-start)]=admittances[start:stop]
        result[:,start:stop]=model.lu.solve(rhs)[output_buses,:]
    return result
