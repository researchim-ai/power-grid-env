"""Separate sparse Newton using original PP electrical stamps/derivatives.

Same declared clipped P / projected Q policy, separately implemented equations
and step loop. Does not call native residual, Jacobian, initializer or audits.
"""
import numpy as np
from scipy.sparse import bmat,csc_matrix,diags
from scipy.sparse.linalg import splu


class SparseReference:
    def __init__(self,case,options):
        import pandapower as pp
        from pandapower.pypower.makeYbus import makeYbus
        from pandapower.pypower.idx_brch import branch_cols
        if pp.__version__!='3.5.6':raise ValueError('pinned pandapower 3.5.6 required')
        if any(g.pg is None or not g.pmin<=g.pg<=g.pmax for g in case.generators):raise ValueError('reference contract requires Pg schedules inside P bounds')
        self.case=case;self.n=len(case.buses);self.idx={b.id:i for i,b in enumerate(case.buses)};self.base=case.base_mva
        ref=next(b.id for b in case.buses if b.type_code==3) if options.get('angle_reference_bus') is None else options['angle_reference_bus']
        self.ref=self.idx[ref];self.angles=np.array([i for i in range(self.n) if i!=self.ref],int)
        bus=np.array([[i,b.type_code,b.pd,b.qd,b.gs*self.base,b.bs*self.base,1,b.vm,b.va_deg,1,1,b.vmax,b.vmin] for i,b in enumerate(case.buses)],float)
        branch=np.zeros((len(case.branches),branch_cols))
        for i,b in enumerate(case.branches):branch[i,:13]=[self.idx[b.fbus],self.idx[b.tbus],b.r,b.x,b.b,b.rate_a,0,0,b.ratio,b.angle_deg,b.status,b.angmin_deg,b.angmax_deg]
        self.y,self.yf,self.yt=makeYbus(self.base,bus,branch)
        self.alpha=np.zeros(len(case.generators));total=sum(p['weight'] for p in options['participants'])
        for p in options['participants']:self.alpha[p['generator_index']]=p['weight']/total
        self.pg0=np.array([g.pg for g in case.generators])/self.base;self.plo=np.array([g.pmin for g in case.generators])/self.base;self.phi=np.array([g.pmax for g in case.generators])/self.base
        self.genbus=np.array([self.idx[g.bus] for g in case.generators],int)
        self.ql=np.zeros(self.n);self.qh=np.zeros(self.n);self.q0=np.zeros(self.n)
        for i,g in enumerate(case.generators):
            b=self.genbus[i];self.ql[b]+=g.qmin/self.base;self.qh[b]+=g.qmax/self.base;self.q0[b]+=(g.qg or 0.)/self.base
        self.control=np.array([b.type_code in (2,3) for b in case.buses]);self.vset=np.array([b.vm for b in case.buses]);self.pd=np.array([b.pd for b in case.buses])/self.base;self.qd=np.array([b.qd for b in case.buses])/self.base

    def unpack(self,x):
        a=np.zeros(self.n);a[self.angles]=x[:self.n-1];return x[self.n-1:-1],a,x[-1]

    def evaluate(self,x,jacobian=False):
        vm,a,delta=self.unpack(x);v=vm*np.exp(1j*a);s=v*np.conj(self.y@v)
        raw=self.pg0+self.alpha*delta;pg=np.minimum(self.phi,np.maximum(self.plo,raw));injection=np.bincount(self.genbus,weights=pg,minlength=self.n)
        q=s.imag+self.qd;z=q-(vm-self.vset);fq=q-self.q0
        fq[self.control]=q[self.control]-np.minimum(self.qh[self.control],np.maximum(self.ql[self.control],z[self.control]))
        residual=np.r_[s.real+self.pd-injection,fq]
        if not jacobian:return residual
        from pandapower.pypower.dSbus_dV import dSbus_dV
        dv,da=dSbus_dV(self.y,v)
        voltage_mode=self.control&(self.qh>self.ql)&(z>=self.ql)&(z<=self.qh)
        active_p=(self.phi>self.plo)&(raw>=self.plo)&(raw<=self.phi)
        weights=np.bincount(self.genbus,weights=self.alpha*active_p,minlength=self.n)
        qrows=diags((~voltage_mode).astype(float))
        j=bmat([[da.real[:,self.angles],dv.real,csc_matrix(-weights[:,None])],
                [qrows@da.imag[:,self.angles],qrows@dv.imag+diags(voltage_mode.astype(float)),csc_matrix((self.n,1))]],format='csc')
        return residual,j

    def render(self,x):
        vm,a,delta=self.unpack(x);v=vm*np.exp(1j*a);s=v*np.conj(self.y@v);pg=np.clip(self.pg0+self.alpha*delta,self.plo,self.phi)*self.base
        busp=np.bincount(self.genbus,weights=pg,minlength=self.n);busq=np.where(self.control,s.imag+self.qd,self.q0)*self.base
        source=np.array([self.idx[b.fbus] for b in self.case.branches]);target=np.array([self.idx[b.tbus] for b in self.case.branches])
        sf=v[source]*np.conj(self.yf@v)*self.base;st=v[target]*np.conj(self.yt@v)*self.base
        return dict(voltage_pu=vm.tolist(),angle_rad=a.tolist(),generation_by_bus=[[b.id,float(busp[i]),float(busq[i])] for i,b in enumerate(self.case.buses)],
            branch_power_mva=np.column_stack([sf.real,sf.imag,st.real,st.imag]).tolist(),pg_mw=pg.tolist(),delta_mw=float(delta*self.base))


def classic_seed(model):
    """Separate classical PF initialization using PP dSbus_dV, not native PF."""
    from pandapower.pypower.dSbus_dV import dSbus_dV
    a=np.deg2rad([b.va_deg for b in model.case.buses]);a-=a[model.ref];vm=np.array([b.vm for b in model.case.buses])
    reference=next(i for i,b in enumerate(model.case.buses) if b.type_code==3)
    nonref=np.array([i for i in range(model.n) if i!=reference],int);pq=np.array([i for i,b in enumerate(model.case.buses) if b.type_code==1],int)
    planned=np.bincount(model.genbus,weights=model.pg0,minlength=model.n)
    def residual(v,t):
        voltage=v*np.exp(1j*t);s=voltage*np.conj(model.y@voltage)
        return np.r_[s.real[nonref]+model.pd[nonref]-planned[nonref],s.imag[pq]+model.qd[pq]-model.q0[pq]]
    for iteration in range(61):
        f=residual(vm,a);norm=float(max(abs(f),default=0.))
        if np.isfinite(norm) and norm<=1e-10:
            v=vm*np.exp(1j*a);delta=float(np.sum((v*np.conj(model.y@v)).real+model.pd)-sum(model.pg0))
            a-=a[model.ref];return np.r_[a[model.angles],vm,delta]
        if iteration==60:break
        dv,da=dSbus_dV(model.y,vm*np.exp(1j*a));j=bmat([[da.real[nonref,:][:,nonref],dv.real[nonref,:][:,pq]],[da.imag[pq,:][:,nonref],dv.imag[pq,:][:,pq]]],format='csc')
        try:step=splu(j).solve(-f)
        except RuntimeError:break
        changed=False
        for factor in [1.,.5,.25,.125,.0625,.03125,.015625,.0078125]:
            v=vm.copy();t=a.copy();t[nonref]+=factor*step[:len(nonref)];v[pq]+=factor*step[len(nonref):]
            if np.all((v>.02)&(v<3.)) and max(abs(residual(v,t)),default=0.)<norm:vm=v;a=t;changed=True;break
        if not changed:break
    return None


def solve_reference(case,options):
    model=SparseReference(case,options);angles=np.deg2rad([b.va_deg for b in case.buses]);angles-=angles[model.ref]
    x=np.r_[angles[model.angles],[b.vm for b in case.buses],(sum(b.pd for b in case.buses)-sum(g.pg for g in case.generators))/case.base_mva]
    initialized=classic_seed(model)
    seed_policy='separate classical PF using PP electrical derivatives' if initialized is not None else 'declared case Vm/angles after separate classical PF failure'
    if initialized is not None:x=initialized
    largest=0
    for iteration in range(81):
        f,j=model.evaluate(x,True);norm=float(max(abs(f),default=0.));largest=max(largest,j.nnz)
        if np.isfinite(norm) and norm<=1e-10:return dict(result=model.render(x),maximum_residual_pu=norm,iterations=iteration,maximum_jacobian_nnz=largest,seed=seed_policy)
        if iteration==80:break
        try:step=splu(j).solve(-f)
        except RuntimeError:break
        improved=False
        for factor in [1.,.5,.25,.125,.0625,.03125,.015625,.0078125]:
            trial=x+factor*step;vm,_,_=model.unpack(trial)
            if np.isfinite(trial).all() and np.all((vm>.02)&(vm<3.)) and max(abs(model.evaluate(trial)),default=0.)<norm:x=trial;improved=True;break
        if not improved:break
    raise ValueError('separate sparse reference did not converge: '+str(norm))
