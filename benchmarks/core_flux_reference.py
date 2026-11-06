"""Dense current-KCL network using the unmodified motulator core-loss RHS.

The original compute_outputs is queried at zero/one volt to recover its affine
terminal relation; no native blocked-converter AC port is used (it excludes Gc).
Pure RL junction rows use differentiated KCL. No production network code is used.
"""
from types import SimpleNamespace
import numpy as np
from benchmarks.flux_network_native import NativeCircuit,_complex


class NativeCoreCircuit(NativeCircuit):
    conductive_rows=True
    def __init__(self,network,settings):
        if not all(p.core_loss_s>0 for p in settings.motors):raise ValueError('core reference requires positive-Gc motors')
        super().__init__(network,settings)
        for p,_,_,m,_ in self.entries:m.par.G_c=p.core_loss_s
        self.kcomplex=(self.B/self.l)@self.B.conj().T

    def quantities(self,t,x):
        edge=_complex(x[:2*len(self.l)]);outgoing=self.B@edge;g=np.zeros(len(self.names));j=np.zeros(len(self.names),complex)
        for _,b,conductance in self.passive_conductance:g[b]+=conductance
        for p,b,a,m,_ in self.entries:
            speed=a+2*(len(p.cages)+1);m.state.psi_s_ab=complex(*x[a:a+2]);rotor=_complex(x[a+2:speed]);m.state.psi_r_ab=rotor[0] if len(p.cages)==1 else rotor;m.inp.w_M=x[speed]
            if p.thermal:
                m.par.R_s=p.stator_r_ohm*(1+p.thermal.alpha_per_c*(x[speed+1]-p.thermal.reference_c))
                m.par.R_r=(p.cages[0].resistance_ohm if len(p.cages)==1 else np.array([c.resistance_ohm for c in p.cages]))*(1+p.thermal.alpha_per_c*(x[speed+2]-p.thermal.reference_c))
            m.inp.u_s_ab=0j;zero=m.compute_outputs(m.state,m.inp)[0]
            m.inp.u_s_ab=1+0j;one=m.compute_outputs(m.state,m.inp)[0]
            g[b]+=(one-zero).real;j[b]+=zero
        matrix=self.kcomplex.copy();rhs=self.B@(self.r/self.l*edge)
        for b in range(len(self.names)):
            if g[b]>0:matrix[b]=0.;matrix[b,b]=g[b];rhs[b]=-outgoing[b]-j[b]
        for source,b,index in self.sources:
            emf=source.ll_v*np.sqrt(2/3)*self.scales[source.id]*np.exp(1j*(self.w*t+source.angle_a_rad));matrix[b]=0.
            if index is None:matrix[b,b]=1+source.r_ohm*g[b];rhs[b]=emf-source.r_ohm*(outgoing[b]+j[b])
            else:matrix[b,b]=1.;rhs[b]=emf
        v=np.linalg.solve(matrix,rhs);ports=[]
        for p,b,a,m,_ in self.entries:
            m.inp.u_s_ab=v[b];current=m.compute_outputs(m.state,m.inp)[0];outgoing[b]+=current;ports.append(SimpleNamespace(i=current))
        for _,b,conductance in self.passive_conductance:outgoing[b]+=conductance*v[b]
        kcl=max(abs(outgoing[self.interior]),default=0.)/max(1.,max(abs(outgoing),default=0.),max(abs(edge),default=0.))
        return edge,v,ports,float(kcl),outgoing

    def rhs(self,t,x):
        dx=super().rhs(t,x)
        for p,b,a,m,_ in self.entries:
            if p.thermal:
                ds=m.inp.u_s_ab-m.par.R_s*m.out.i_s_ab
                dx[a+2*(len(p.cages)+1)+1]+=1.5*p.core_loss_s*abs(ds)**2/p.thermal.stator_capacity_j_per_k
        return dx

    def outputs(self,t,x):
        row,kcl=super().outputs(t,x)
        _,v,_,_,_=self.quantities(t,x)
        for id_,b,g in self.passive_conductance:
            current=g*v[b];row[id_+'.current_alpha_a']=current.real;row[id_+'.current_beta_a']=current.imag;row[id_+'.loss_w']=float(1.5*g*abs(v[b])**2)
        for p,b,a,m,_ in self.entries:
            ds=m.inp.u_s_ab-m.par.R_s*m.out.i_s_ab;row[p.id+'.core_loss_w']=float(1.5*p.core_loss_s*abs(ds)**2)
        return row,kcl
