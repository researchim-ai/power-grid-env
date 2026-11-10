"""Independent electrothermal all-flux ODE; no production thermal/machine calls."""
import numpy as np
from scipy.integrate import solve_ivp


def reference(passport,initial_flux,initial_temperature,speed,times):
    p=passport;h=p['thermal'];n=1+len(p['cages']);at=2*n;leak=np.array([c['leakage_h'] for c in p['cages']]);rr0=np.array([c['resistance_ohm'] for c in p['cages']]);gc=p.get('core_loss_s',0.)
    def rhs(t,x):
        z=x[:at].reshape(n,2);psi=z[:,0]+1j*z[:,1];ts,tr=x[at:at+2];rs=p['stator_r_ohm']*(1+h['alpha_per_c']*(ts-h['reference_c']));rr=rr0*(1+h['alpha_per_c']*(tr-h['reference_c']))
        ir=(psi[1:]-psi[0])/leak;magnetic=psi[0]*(1+(p['saturation_beta_per_vs']*abs(psi[0]))**p['saturation_exponent'])/p['unsaturated_h']-sum(ir)
        u=180*np.exp(1j*(2*np.pi*40*t+.2));is_=(magnetic+gc*u)/(1+rs*gc);ds=u-rs*is_;dr=-rr*ir+1j*p['pole_pairs']*speed*psi[1:];rate=np.r_[ds,dr]
        stator=1.5*(rs*abs(is_)**2+gc*abs(ds)**2);rotor=1.5*sum(rr*abs(ir)**2);cross=h['cross_w_per_k']*(ts-tr);hs=h['stator_ambient_w_per_k']*(ts-h['ambient_c']);hr=h['rotor_ambient_w_per_k']*(tr-h['ambient_c'])
        thermal=[(stator-hs-cross)/h['stator_capacity_j_per_k'],(rotor-hr+cross)/h['rotor_capacity_j_per_k']]
        torque=1.5*p['pole_pairs']*(magnetic*np.conj(psi[0])).imag;power=1.5*(u*np.conj(is_)).real
        return np.r_[np.column_stack([rate.real,rate.imag]).ravel(),thermal,power,hs+hr+torque*speed,stator,rotor]
    y=np.r_[initial_flux,initial_temperature,0.,0.,0.,0.]
    result=solve_ivp(rhs,(0.,max(times)),y,method='DOP853',rtol=1e-11,atol=1e-12,max_step=5e-6,dense_output=True)
    if not result.success:raise ValueError('independent electrothermal ODE failed')
    v=result.sol(times)
    return dict(time_s=list(times),flux=v[:at].T.tolist(),temperature_c=v[at:at+2].T.tolist(),source_j=v[-4].tolist(),ambient_and_shaft_j=v[-3].tolist(),stator_heat_j=v[-2].tolist(),rotor_heat_j=v[-1].tolist(),shaft_speed_rad_s=speed,scope='separate continuous electrothermal winding ODE, prescribed rotating voltage; not full switched-drive controller/network')
