"""Separate sparse CPF controller on PP electrical equations; no native driver.

Uses its own initializer, tangent, corrector, rollback and turn bisection.
It is an equation-level reference, not pandapower's own continuation product.
"""
import math
import numpy as np
from scipy.sparse import bmat,csc_matrix
from scipy.sparse.linalg import splu
from benchmarks.ac_dispatch_sparse_reference import SparseReference,solve_reference


class ReferenceArc:
    def __init__(self,start,target,options):
        self.model=SparseReference(start,options);self.start=start;self.target=target;self.size=2*len(start.buses);self.scale=math.sqrt(self.size)
        self.p0=self.model.pd.copy();self.q0=self.model.qd.copy()
        self.dp=np.array([b.pd-a.pd for a,b in zip(start.buses,target.buses)])/start.base_mva
        self.dq=np.array([b.qd-a.qd for a,b in zip(start.buses,target.buses)])/start.base_mva

    def evaluate(self,u):
        x=u[:-1]*self.scale;lam=u[-1];m=self.model;m.pd=self.p0+lam*self.dp;m.qd=self.q0+lam*self.dq
        f,j=m.evaluate(x,True);vm,a,_=m.unpack(x);v=vm*np.exp(1j*a);q=(v*np.conj(m.y@v)).imag+m.qd;z=q-(vm-m.vset)
        voltage=m.control&(m.qh>m.ql)&(z>=m.ql)&(z<=m.qh)
        g=np.r_[self.dp,np.where(voltage,0.,self.dq)]
        return f,bmat([[j*self.scale,csc_matrix(g[:,None])]],format='csc')

    def valid(self,u):
        vm,_,_=self.model.unpack(u[:-1]*self.scale)
        return bool(np.isfinite(u).all() and -.1<=u[-1]<=1.25 and np.all((vm>.02)&(vm<3.)))

    def tangent(self,u,previous):
        _,a=self.evaluate(u);j=bmat([[a],[csc_matrix(previous[None,:])]],format='csc');rhs=np.r_[np.zeros(self.size),1.]
        t=splu(j).solve(rhs);t/=np.linalg.norm(t)
        return t if t@previous>=0 else -t

    def correct(self,predicted,tangent):
        u=predicted.copy()
        for _ in range(32):
            f,a=self.evaluate(u);r=np.r_[f,(u-predicted)@tangent];norm=max(abs(r))
            if norm<=1e-12:return u
            try:dx=splu(bmat([[a],[csc_matrix(tangent[None,:])]],format='csc')).solve(-r)
            except RuntimeError:return None
            for damping in [1.,.5,.25,.125,.0625,.03125,.015625,.0078125]:
                trial=u+damping*dx
                if self.valid(trial):
                    rf,_=self.evaluate(trial)
                    if max(max(abs(rf)),abs((trial-predicted)@tangent))<norm:u=trial;break
            else:return None
        return None

    def initial(self,options):
        ref=solve_reference(self.start,options)['result'];m=self.model
        x=np.r_[np.array(ref['angle_rad'])[m.angles],ref['voltage_pu'],ref['delta_mw']/self.start.base_mva]
        u=np.r_[x/self.scale,0.];axis=np.r_[np.zeros(self.size),1.]
        u=self.correct(u,axis)
        if u is None:raise ValueError('independent CPF initialization failed')
        return u,self.tangent(u,axis)

    def trace(self,options,step=.01,maximum_points=240):
        u,t=self.initial(options);points=[dict(u=u.tolist(),tangent=t.tolist())];turn=None;after=0;h=step
        for _ in range(maximum_points-1):
            candidate=self.correct(u+h*t,t)
            if candidate is None:
                h/=2
                if h<1e-7:raise ValueError('independent CPF step exhausted')
                continue
            nt=self.tangent(candidate,t)
            if t[-1]>0 and nt[-1]<0 and turn is None:
                left=0.;right=h;best=None
                for _ in range(24):
                    mid=(left+right)/2;z=self.correct(u+mid*t,t)
                    if z is None:raise ValueError('independent turn corrector failed')
                    tz=self.tangent(z,t);best=dict(u=z.tolist(),tangent=tz.tolist())
                    if tz[-1]>0:left=mid
                    else:right=mid
                turn=best
            points.append(dict(u=candidate.tolist(),tangent=nt.tolist()));u=candidate;t=nt;h=min(step,h*1.5)
            if turn is not None:
                after+=1
                if after>=12:break
            if u[-1]>=1 and turn is None:break
        if turn is None:raise ValueError('independent CPF did not observe a turn')
        return dict(points=points,turn=turn,step=step,primary_state_used=False,controller='separate PP-stamp sparse arclength Newton, initialized independently')

    def at_lambda(self,path,lam,side):
        candidates=[p for p in path['points'] if np.sign(p['tangent'][-1])==side]
        if not candidates:raise ValueError('reference branch side unavailable')
        p=min(candidates,key=lambda p:abs(p['u'][-1]-lam));u=np.array(p['u']);u[-1]=lam;axis=np.r_[np.zeros(self.size),1.]
        result=self.correct(u,axis)
        if result is None:raise ValueError('independent fixed-lambda correction failed')
        tangent=self.tangent(result,np.array(p['tangent']))
        if np.sign(tangent[-1])!=side:raise ValueError('independent correction changed branch side')
        self.evaluate(result)
        return dict(result=self.model.render(result[:-1]*self.scale),maximum_residual_pu=float(max(abs(self.evaluate(result)[0]))),seed='own independent continuation branch, no primary voltage/angle state')
