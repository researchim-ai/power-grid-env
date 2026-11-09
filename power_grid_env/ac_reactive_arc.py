"""Fixed-dimension projected reactive-control equations for AC continuation.

PV controllers use Q - clip(Q - (Vm - Vset), Qmin, Qmax) = 0 in pu.
This enforces the voltage/Q complementarity graph and permits limit release.
The slack retains its voltage and balances Q without an enforced Q limit.
At a kink the selected generalized derivative is the voltage-control side.
"""
import numpy as np
from scipy.sparse import bmat,csc_matrix,diags
from power_grid_env.ac_pseudo_arc import PolarFamily
from power_grid_env.balanced_sparse import derivatives


class ReactivePolarFamily(PolarFamily):
    def __init__(self,start,target):
        super().__init__(start,target)
        self.pq=self.nonref.copy()
        self.size=2*len(self.nonref);self.scale=np.sqrt(max(1,self.size))
        self.pv=np.array([i for i in self.nonref if start.buses[i].type_code==2],int)
        self.lower=np.zeros(self.n);self.upper=np.zeros(self.n)
        for i in self.pv:
            units=[g for g in start.generators if g.bus==start.buses[i].id]
            if not units:raise ValueError('PV control requires a generator')
            self.lower[i]=sum(g.qmin for g in units)/start.base_mva
            self.upper[i]=sum(g.qmax for g in units)/start.base_mva
        self.g=np.r_[self.dp[self.nonref],self.dq[self.nonref]]/start.base_mva

    def control(self,u):
        vm,a,lam=self.unpack(u);v=vm*np.exp(1j*a)
        q=(v*np.conj(self.y@v)).imag+(np.array([b.qd for b in self.start.buses])+lam*self.dq)/self.start.base_mva
        z=q-(vm-np.array([b.vm for b in self.start.buses]))
        modes={self.start.buses[i].id:('fixed' if self.lower[i]==self.upper[i] else 'lower' if z[i]<self.lower[i] else 'upper' if z[i]>self.upper[i] else 'voltage') for i in self.pv}
        return q,z,modes

    def evaluate(self,u,jacobian=False):
        vm,a,lam=self.unpack(u);v=vm*np.exp(1j*a);power=v*np.conj(self.y@v)
        pd=np.array([b.pd for b in self.start.buses])+lam*self.dp
        q,z,modes=self.control(u)
        fq=q-self.qg/self.start.base_mva
        free=np.zeros(self.n,dtype=bool)
        for i in self.pv:
            fq[i]=q[i]-np.clip(z[i],self.lower[i],self.upper[i])
            free[i]=modes[self.start.buses[i].id]=='voltage'
        f=np.r_[power.real[self.nonref]-(self.pg[self.nonref]-pd[self.nonref])/self.start.base_mva,fq[self.nonref]]
        # The lambda column depends on the current active control equations.
        self.g=np.r_[self.dp[self.nonref],np.where(free[self.nonref],0.,self.dq[self.nonref])]/self.start.base_mva
        if not jacobian:return f
        pa,qa,pv,qv=derivatives(vm,a,self.y);rows=self.nonref
        keep=diags((~free[rows]).astype(float));voltage=diags(free[rows].astype(float))
        j=bmat([[pa[rows,:][:,rows],pv[rows,:][:,rows]],
                [keep@qa[rows,:][:,rows],keep@qv[rows,:][:,rows]+voltage]],format='csc')*self.scale
        return f,j
