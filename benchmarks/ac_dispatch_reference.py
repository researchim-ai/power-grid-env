"""PP 3.5.6 electrical stamps + SciPy numerical-Jacobian root, separate controller.

No primary dispatch residual/Jacobian/seed is called. The mathematical clipped
participation and projected Q policy are shared as the declared study contract.
"""
import numpy as np
from scipy.optimize import root


def solve_reference(case,options):
    import pandapower as pp
    from pandapower.pypower.makeYbus import makeYbus
    from pandapower.pypower.idx_brch import branch_cols
    if pp.__version__!='3.5.6':raise ValueError('pandapower 3.5.6 required')
    n=len(case.buses);idx={b.id:i for i,b in enumerate(case.buses)};base=case.base_mva
    anchor=idx[next(b.id for b in case.buses if b.type_code==3) if options.get('angle_reference_bus') is None else options['angle_reference_bus']]
    others=[i for i in range(n) if i!=anchor]
    bus=np.array([[i,b.type_code,b.pd,b.qd,b.gs*base,b.bs*base,1,b.vm,b.va_deg,1,1,b.vmax,b.vmin] for i,b in enumerate(case.buses)],float)
    branch=np.zeros((len(case.branches),branch_cols))
    for i,b in enumerate(case.branches):branch[i,:13]=[idx[b.fbus],idx[b.tbus],b.r,b.x,b.b,b.rate_a,0,0,b.ratio,b.angle_deg,b.status,b.angmin_deg,b.angmax_deg]
    y,yf,yt=makeYbus(base,bus,branch)
    weights=np.zeros(len(case.generators));total=sum(p['weight'] for p in options['participants'])
    for p in options['participants']:weights[p['generator_index']]=p['weight']/total
    control=[b.type_code in (2,3) for b in case.buses]
    units=[[i for i,g in enumerate(case.generators) if g.bus==b.id] for b in case.buses]
    def equations(x,render=False):
        a=np.zeros(n);a[others]=x[:n-1];vm=x[n-1:2*n-1];v=vm*np.exp(1j*a);power=v*np.conj(y@v)*base
        pg=[min(g.pmax,max(g.pmin,g.pg+weights[i]*x[-1])) for i,g in enumerate(case.generators)]
        p=[];q=[];qgen=[]
        for j,b in enumerate(case.buses):
            p.append((power[j].real+b.pd-sum(pg[i] for i in units[j]))/base)
            needed=power[j].imag+b.qd;qgen.append(float(needed))
            if control[j]:
                lo=sum(case.generators[i].qmin for i in units[j]);hi=sum(case.generators[i].qmax for i in units[j])
                auxiliary=needed-base*(vm[j]-b.vm)
                q.append((needed-min(hi,max(lo,auxiliary)))/base)
            else:q.append((needed-sum(case.generators[i].qg or 0. for i in units[j]))/base)
        if not render:return np.r_[p,q]
        gen=[]
        for j,b in enumerate(case.buses):gen.append([b.id,float(sum(pg[i] for i in units[j])),qgen[j] if control[j] else float(sum(case.generators[i].qg or 0. for i in units[j]))])
        flows=[]
        for k,b in enumerate(case.branches):
            sf=v[idx[b.fbus]]*np.conj((yf@v)[k])*base;st=v[idx[b.tbus]]*np.conj((yt@v)[k])*base
            flows.append([float(sf.real),float(sf.imag),float(st.real),float(st.imag)])
        return dict(voltage_pu=vm.tolist(),angle_rad=a.tolist(),generation_by_bus=gen,branch_power_mva=flows,pg_mw=[float(x) for x in pg],delta_mw=float(x[-1]))
    guess=np.r_[np.zeros(n-1),[b.vm for b in case.buses],sum(b.pd for b in case.buses)-sum(g.pg for g in case.generators)]
    solved=root(equations,guess,method='hybr',options=dict(xtol=1e-10,maxfev=12000));res=float(max(abs(equations(solved.x)),default=0.))
    if not np.isfinite(solved.x).all() or res>1e-9 or not np.all((solved.x[n-1:2*n-1]>.02)&(solved.x[n-1:2*n-1]<3.)):
        raise ValueError('independent dispatch reference did not converge: '+str(res))
    return dict(result=equations(solved.x,True),maximum_residual_pu=res,evaluations=int(solved.nfev),reported_solver_success=bool(solved.success))
