"""Independent flux-coordinate multi-cage equations, not native motulator.

Explicit currents from fluxes replace the production inverse magnetic map.
Discrete passive RL cages are authored stress parameters unless fitted data is
supplied; they do not constitute an identified OEM skin-depth model.
"""
from types import SimpleNamespace
import numpy as np


class CageFluxMachine:
    def __init__(self,p):
        self.p=p;self.par=SimpleNamespace(R_s=p.stator_r_ohm,R_r=np.array([c.resistance_ohm for c in p.cages]),G_c=0.)
        self.state=SimpleNamespace(psi_s_ab=0j,psi_r_ab=np.zeros(len(p.cages),complex))
        self.inp=SimpleNamespace(w_M=0.,u_s_ab=0j);self.out=SimpleNamespace()
        self.leakage=np.array([c.leakage_h for c in p.cages])

    def compute_outputs(self,state,inp):
        p=self.p;psi=state.psi_s_ab;rotor=(state.psi_r_ab-psi)/self.leakage
        magnetic=psi*(1+(p.saturation_beta_per_vs*abs(psi))**p.saturation_exponent)/p.unsaturated_h-sum(rotor)
        current=(magnetic+self.par.G_c*inp.u_s_ab)/(1+self.par.G_c*self.par.R_s)
        return current,rotor,1.5*p.pole_pairs*(magnetic*np.conj(psi)).imag

    def set_outputs(self,t):self.out.i_s_ab,self.out.i_r_ab,self.out.tau_M=self.compute_outputs(self.state,self.inp)

    def rhs(self,t):
        return [self.inp.u_s_ab-self.par.R_s*self.out.i_s_ab,-self.par.R_r*self.out.i_r_ab+1j*self.p.pole_pairs*self.inp.w_M*self.state.psi_r_ab]

    def compute_ac_port(self):
        if self.par.G_c:raise ValueError('use the conductive algebraic terminal relation for positive Gc')
        psi=self.state.psi_s_ab;p=self.p;rho=abs(psi);s=(p.saturation_beta_per_vs*rho)**p.saturation_exponent
        Y=np.eye(2)*((1+s)/p.unsaturated_h+sum(1/self.leakage))
        if rho:
            v=np.array([psi.real,psi.imag])/rho;Y+=p.saturation_exponent*s/p.unsaturated_h*np.outer(v,v)
        current,ir,_=self.compute_outputs(self.state,self.inp)
        dr=-self.par.R_r*ir+1j*p.pole_pairs*self.inp.w_M*self.state.psi_r_ab
        part=sum(dr/self.leakage);e=self.par.R_s*current+complex(*np.linalg.solve(Y,[part.real,part.imag]))
        return SimpleNamespace(i=current,e=e,Y=Y)
