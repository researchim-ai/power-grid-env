"""Distributed balancing family for the shared sparse branch continuation."""
import math
import numpy as np
from power_grid_env.ac_pseudo_arc import PolarFamily
from power_grid_env.ac_continuation import compatible
from power_grid_env.ac_dispatch import DispatchSystem,audit_dispatch
from power_grid_env.balanced_ac import branch_results


class DispatchFamily(PolarFamily):
    def __init__(self,start,target,options):
        compatible(start,target);self.start=start;self.target=target;self.options=options
        self.system=DispatchSystem(start,options);self.n=len(start.buses);self.size=2*self.n;self.scale=math.sqrt(self.size)
        self.dp=np.array([b.pd-a.pd for a,b in zip(start.buses,target.buses)])
        self.dq=np.array([b.qd-a.qd for a,b in zip(start.buses,target.buses)])
        self.g=np.zeros(self.size);self._lambda=None

    def at(self,lam):
        if self._lambda!=lam:
            case=self.load_case(lam);self.system.case=case
            self.system.pd=np.array([b.pd for b in case.buses])/case.base_mva
            self.system.qd=np.array([b.qd for b in case.buses])/case.base_mva
            self._lambda=lam
        return self.system

    def pack(self,r,lam):
        if 'delta_mw' not in r:raise ValueError('distributed branch state requires delta_mw')
        return np.r_[self.system.pack(r,r['delta_mw']/self.start.base_mva)/self.scale,lam]

    def unpack(self,u):
        vm,a,_=self.system.unpack(u[:-1]*self.scale)
        return vm,a,float(u[-1])

    def valid(self,u,o):
        return bool(u.shape==(self.size+1,) and self.system.valid(u[:-1]*self.scale) and o.minimum_lambda<=u[-1]<=o.maximum_lambda)

    def evaluate(self,u,jacobian=False):
        sys=self.at(float(u[-1]));x=u[:-1]*self.scale
        vm,a,_=sys.unpack(x);v=vm*np.exp(1j*a);q=(v*np.conj(sys.y@v)).imag+sys.qd;z=q-(vm-sys.setpoint)
        free=sys.controlled&(sys.hi>sys.lo)&(z>=sys.lo)&(z<=sys.hi)
        self.g=np.r_[self.dp,np.where(free,0.,self.dq)]/self.start.base_mva
        if jacobian:
            f,j=sys.evaluate(x,True);return f,j*self.scale
        return sys.evaluate(x)

    def result(self,u):return self.at(float(u[-1])).result(u[:-1]*self.scale)

    def control(self,u):
        sys=self.at(float(u[-1]));vm,a,_=sys.unpack(u[:-1]*self.scale);v=vm*np.exp(1j*a)
        q=(v*np.conj(sys.y@v)).imag*sys.case.base_mva+np.array([b.qd for b in sys.case.buses])
        z=q/sys.case.base_mva-(vm-sys.setpoint)
        return q,z,{b.id:('fixed' if sys.lo[i]==sys.hi[i] else 'lower' if z[i]<sys.lo[i] else 'upper' if z[i]>sys.hi[i] else 'voltage') for i,b in enumerate(sys.case.buses) if sys.controlled[i]}

    def p_control(self,u):
        sys=self.at(float(u[-1]));_,_,raw=sys.generation(u[:-1]*self.scale)
        return {i:('fixed' if lo==hi else 'lower' if r<lo else 'upper' if r>hi else 'participating' if w>0 else 'scheduled') for i,(r,lo,hi,w) in enumerate(zip(raw,sys.plo,sys.phi,sys.alpha))}

    def has_p_reserve(self,u):return bool(np.any(self.at(float(u[-1])).generation(u[:-1]*self.scale)[1]))

    def audit(self,result,lam):
        if not result.get('completed'):return None
        case=self.load_case(lam);r=dict(result)
        try:return audit_dispatch(case,self.options,r,branches_required=False)
        except ValueError:return dict(balance_acceptable=False)
