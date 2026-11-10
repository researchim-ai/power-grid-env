"""Flux-state isolated-star Gamma motor with a passive eddy-current branch.

i_s = (i_m - sum(i_r) + Gc*u_s)/(1 + Gc*Rs).
The eddy current is excluded from torque. Terminal feedthrough must be stamped
into network KCL; treating this port as a state-only current source is incorrect.
Equations: Aalto motulator Induction Machines, Core Losses, equation (5).
"""
import numpy as np


class PhaseCoreMotor:
    def __init__(self,parameters):
        self.p=parameters.passport;self.initial_flux=parameters.initial_fluxes_ab_vs
        self.n=1+len(self.p.cages);self.at=2*self.n;self.size=self.at+1+(2 if self.p.thermal else 0)
        self.leakage=np.array([c.leakage_h for c in self.p.cages]);self.rr0=np.array([c.resistance_ohm for c in self.p.cages])

    def initial(self):
        x=np.zeros(self.size);x[self.at]=self.p.initial_speed_rad_s
        if self.initial_flux is not None:x[:self.at]=np.array(self.initial_flux).ravel()
        if self.p.thermal:x[self.at+1:]=self.p.thermal.ambient_c
        return x

    def internal(self,x):
        p=self.p;z=x[:self.at].reshape(-1,2);flux=z[:,0]+1j*z[:,1];psi=flux[0];ir=(flux[1:]-psi)/self.leakage
        if p.thermal:
            temp=x[self.at+1:];factor=1+p.thermal.alpha_per_c*(temp-p.thermal.reference_c)
            if np.any(factor<=0) or max(temp)>p.thermal.maximum_c:raise ValueError('winding temperature outside passport range')
        else:factor=np.ones(2)
        rs=p.stator_r_ohm*factor[0];rr=self.rr0*factor[1];sat=(p.saturation_beta_per_vs*abs(psi))**p.saturation_exponent
        magnetic=psi*(1+sat)/p.unsaturated_h-sum(ir)
        return psi,flux[1:],ir,magnetic,float(rs),rr,float(sat)

    def terminal_relation(self,x):
        _,_,_,magnetic,rs,_,_=self.internal(x);denominator=1+self.p.core_loss_s*rs
        return magnetic/denominator,self.p.core_loss_s/denominator

    def quantities(self,x,voltage):
        p=self.p;psi,rotor,ir,magnetic,rs,rr,sat=self.internal(x);current=(magnetic+p.core_loss_s*voltage)/(1+p.core_loss_s*rs);ds=voltage-rs*current
        return dict(psi=psi,rotor_flux=rotor,current=current,rotor_current=ir,energy_j=float(1.5/p.unsaturated_h*(abs(psi)**2/2+sat*abs(psi)**2/(p.saturation_exponent+2))+.75*sum(self.leakage*abs(ir)**2)),torque_nm=float(1.5*p.pole_pairs*(magnetic*np.conj(psi)).imag),stator_copper_w=float(1.5*rs*abs(current)**2),rotor_copper_w=float(1.5*sum(rr*abs(ir)**2)),core_loss_w=float(1.5*p.core_loss_s*abs(ds)**2),stator_flux_derivative=ds,rotor_flux_derivative=-rr*ir+1j*p.pole_pairs*x[self.at]*rotor)

    def rates(self,t,voltage,x):
        p=self.p;q=self.quantities(x,voltage);w=x[self.at];thermal=np.zeros(2);ambient=0.
        load=p.fan_nm_per_rad_s2*w*abs(w)+p.viscous_nm_per_rad_s*w
        dw=0. if p.locked_rotor else (q['torque_nm']-load)/p.inertia_kg_m2
        copper=q['stator_copper_w']+q['rotor_copper_w'];stored=q['energy_j']+.5*p.inertia_kg_m2*w*w
        if p.thermal:
            h=p.thermal;ts,tr=x[self.at+1:];cross=h.cross_w_per_k*(ts-tr);hs=h.stator_ambient_w_per_k*(ts-h.ambient_c);hr=h.rotor_ambient_w_per_k*(tr-h.ambient_c)
            thermal=np.array([(q['stator_copper_w']+q['core_loss_w']-hs-cross)/h.stator_capacity_j_per_k,(q['rotor_copper_w']-hr+cross)/h.rotor_capacity_j_per_k]);ambient=hs+hr
            stored+=h.stator_capacity_j_per_k*(ts-h.ambient_c)+h.rotor_capacity_j_per_k*(tr-h.ambient_c)
        flux_rate=np.r_[q['stator_flux_derivative'],q['rotor_flux_derivative']];dx=np.r_[np.column_stack([flux_rate.real,flux_rate.imag]).ravel(),dw,thermal if p.thermal else []]
        info=dict(motor_power_w=float(1.5*(voltage*np.conj(q['current'])).real),motor_speed_rad_s=float(w),torque_nm=q['torque_nm'],current_alpha_a=float(q['current'].real),current_beta_a=float(q['current'].imag),rotor_copper_w=q['rotor_copper_w'],stator_copper_w=q['stator_copper_w'],core_loss_w=q['core_loss_w'],stator_flux_alpha_vs=float(q['psi'].real),stator_flux_beta_vs=float(q['psi'].imag),stored_j=float(stored),loss_w=float(ambient if p.thermal else copper+q['core_loss_w']),mechanical_power_w=float(load*w))
        if p.thermal:info.update(stator_temperature_c=float(x[self.at+1]),rotor_temperature_c=float(x[self.at+2]))
        return dx,info
