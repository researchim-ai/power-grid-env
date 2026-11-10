"""Separate continuous all-flux ODE oracle, no production machine/control calls."""
import numpy as np
from scipy.integrate import solve_ivp


def reference(passport,initial_flux,speed,times):
    p=passport;n=1+len(p['cages']);leak=np.array([c['leakage_h'] for c in p['cages']]);rr=np.array([c['resistance_ohm'] for c in p['cages']]);gc=p.get('core_loss_s',0.)
    def voltage(t):return 180*np.exp(1j*(2*np.pi*40*t+.2))
    def evaluate(t,flux):
        f=flux.reshape(n,2);psi=f[:,0]+1j*f[:,1];ir=(psi[1:]-psi[0])/leak
        magnetic=psi[0]*(1+(p['saturation_beta_per_vs']*abs(psi[0]))**p['saturation_exponent'])/p['unsaturated_h']-sum(ir)
        u=voltage(t);is_=(magnetic+gc*u)/(1+p['stator_r_ohm']*gc);ds=u-p['stator_r_ohm']*is_;dr=-rr*ir+1j*p['pole_pairs']*speed*psi[1:]
        rate=np.r_[ds,dr];torque=1.5*p['pole_pairs']*(magnetic*np.conj(psi[0])).imag
        heat=1.5*(p['stator_r_ohm']*abs(is_)**2+sum(rr*abs(ir)**2)+gc*abs(ds)**2)
        return np.column_stack([rate.real,rate.imag]).ravel(),1.5*(u*np.conj(is_)).real,heat+torque*speed,1.5*gc*abs(ds)**2
    def rhs(t,x):
        dx,power,loss,core=evaluate(t,x[:2*n]);return np.r_[dx,power,loss,core]
    y=np.r_[initial_flux,0.,0.,0.];r=solve_ivp(rhs,(0.,max(times)),y,method='DOP853',rtol=1e-11,atol=1e-12,max_step=5e-6,dense_output=True)
    if not r.success:raise ValueError('independent winding ODE did not converge')
    states=r.sol(times)
    return dict(time_s=list(times),flux=states[:2*n].T.tolist(),source_j=states[-3].tolist(),heat_and_shaft_j=states[-2].tolist(),core_heat_j=states[-1].tolist(),scope='independent continuous ODE under prescribed rotating voltage and fixed shaft speed; not full drive trajectory')
