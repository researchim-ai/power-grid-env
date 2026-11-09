"""Pinned pandapower stamps/injections/derivatives for a separate CPF branch.

Shares only the continuation controller/coordinates and state rendering.
Does not import production admittance or derivative functions. Base and target
Newton solves use pandapower with its own evolving guesses.
"""
import math
import numpy as np
from scipy.sparse import bmat
from power_grid_env.ac_pseudo_arc import PolarFamily
from power_grid_env.ac_continuation import ContinuationOptions
from benchmarks.ac_continuation_acceptance import reference_solver


class ReferenceFamily(PolarFamily):
    def __init__(self,start,target):
        import pandapower as pp
        from pandapower.pypower.makeYbus import makeYbus
        from pandapower.pypower.makeSbus import makeSbus
        from pandapower.pypower.idx_brch import branch_cols
        if pp.__version__!='3.5.6':raise ValueError('pinned pandapower 3.5.6 required')
        self.start=start;self.target=target;self.n=len(start.buses);self.index={b.id:i for i,b in enumerate(start.buses)}
        self.nonref=np.array([i for i,b in enumerate(start.buses) if b.type_code!=3],int);self.pq=np.array([i for i,b in enumerate(start.buses) if b.type_code==1],int)
        self.size=len(self.nonref)+len(self.pq);self.scale=math.sqrt(max(1,self.size));self.audit_options=ContinuationOptions()
        self.dp=np.array([b.pd-a.pd for a,b in zip(start.buses,target.buses)]);self.dq=np.array([b.qd-a.qd for a,b in zip(start.buses,target.buses)])
        bus=np.array([[i,b.type_code,b.pd,b.qd,b.gs*start.base_mva,b.bs*start.base_mva,1,b.vm,b.va_deg,1,1,b.vmax,b.vmin] for i,b in enumerate(start.buses)],float)
        gen=np.zeros((len(start.generators),21))
        for i,g in enumerate(start.generators):gen[i,:10]=[self.index[g.bus],g.pg or 0,g.qg or 0,g.qmax,g.qmin,start.buses[self.index[g.bus]].vm,start.base_mva,1,g.pmax,g.pmin]
        branch=np.zeros((len(start.branches),branch_cols))
        for i,b in enumerate(start.branches):branch[i,:13]=[self.index[b.fbus],self.index[b.tbus],b.r,b.x,b.b,b.rate_a,0,0,b.ratio,b.angle_deg,b.status,b.angmin_deg,b.angmax_deg]
        self.y,_,_=makeYbus(start.base_mva,bus,branch)
        self.base_s=makeSbus(start.base_mva,bus,gen)
        other=bus.copy();other[:,2]+=self.dp;other[:,3]+=self.dq
        self.delta_s=makeSbus(start.base_mva,other,gen)-self.base_s
        self.g=-np.r_[self.delta_s.real[self.nonref],self.delta_s.imag[self.pq]]

    def evaluate(self,u,jacobian=False):
        from pandapower.pypower.dSbus_dV import dSbus_dV
        vm,a,lam=self.unpack(u);v=vm*np.exp(1j*a)
        residual=v*np.conj(self.y@v)-(self.base_s+lam*self.delta_s)
        f=np.r_[residual.real[self.nonref],residual.imag[self.pq]]
        if not jacobian:return f
        dv,da=dSbus_dV(self.y,v)
        j=bmat([[da.real[self.nonref,:][:,self.nonref],dv.real[self.nonref,:][:,self.pq]],
                [da.imag[self.pq,:][:,self.nonref],dv.imag[self.pq,:][:,self.pq]]],format='csc')*self.scale
        return f,j


class ReferenceBackend:
    name='pandapower-equations-3.5.6'
    family=ReferenceFamily
    def __init__(self,base_document,factor):self.solve=reference_solver(base_document,factor)
