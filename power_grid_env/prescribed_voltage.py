"""Energy-consistent Gamma midpoint for a prescribed terminal-voltage interval.

All rotor cages are retained by exact complex elimination. A two-real-variable
Newton solve handles radial saturation. Shaft and electromagnetic stages iterate
together. This port does not replace an unknown-voltage network Norton solve.
"""
import math
import numpy as np


def gradient(old,mid,lm,beta,exponent):
    """Radial divided difference and its Cartesian 2x2 derivative, as scalars."""
    after=2*mid-old;a=abs(old);b=abs(after);power=exponent+2
    c=.5/lm;gx=gy=0.
    if beta and a+b:
        total=sum(b**(power-1-k)*a**k for k in range(power))
        derivative=sum((power-1-k)*b**(power-2-k)*a**k for k in range(power-1))
        scale=beta**exponent/(power*lm);c+=scale*total/(a+b)
        if b:
            coefficient=2*scale*(derivative*(a+b)-total)/(a+b)**2/b
            gx=coefficient*after.real;gy=coefficient*after.imag
    return 2*c*mid,(2*c+2*mid.real*gx,2*mid.real*gy,2*mid.imag*gx,2*c+2*mid.imag*gy)


def voltage_step(passport,old,speed,voltage,elapsed,load_torque_nm=0.,temperature=None):
    """One midpoint step; optional temperature is imposed midpoint Ts/Tr.

No thermal state is integrated here. The caller must couple those temperatures
externally. Inputs/passport remain unchanged on success or failure.
"""
    p=passport;flat=np.asarray(old,dtype=float)
    if flat.shape!=(2*(1+len(p.cages)),) or not np.all(np.isfinite(flat)) or not all(np.isfinite(x) for x in [speed,voltage,elapsed,load_torque_nm]) or elapsed<=0:raise ValueError('finite matching flux inventory and positive elapsed required')
    if p.thermal and temperature is None:raise ValueError('thermal passport requires explicit imposed midpoint temperature')
    if not p.thermal and temperature is not None:raise ValueError('temperature requires thermal passport')
    if p.locked_rotor and speed!=0:raise ValueError('locked shaft requires zero speed')
    factor_s=factor_r=1.
    if p.thermal:
        theta=np.asarray(temperature,dtype=float)
        if theta.shape!=(2,) or not np.all(np.isfinite(theta)) or np.max(theta)>=p.thermal.maximum_c:raise ValueError('finite two-node midpoint temperature below maximum_c required')
        factor_s,factor_r=1+p.thermal.alpha_per_c*(theta-p.thermal.reference_c)
        if min(factor_s,factor_r)<=0:raise ValueError('positive winding resistance required')
    rs=p.stator_r_ohm*factor_s
    leakage=[c.leakage_h for c in p.cages];resistance=[c.resistance_ohm*factor_r for c in p.cages]
    old_s=complex(flat[0],flat[1]);old_r=[complex(flat[k],flat[k+1]) for k in range(2,len(flat),2)]
    h=elapsed;inverse_time=2/h;wmid=float(speed);ds=0j;iterations=0
    for outer in range(24):
        omega=p.pole_pairs*wmid
        denominator=[complex(inverse_time+r/l,-omega) for r,l in zip(resistance,leakage)]
        offset=[(-r/l*(rotor-old_s)+1j*omega*rotor)/d for rotor,r,l,d in zip(old_r,resistance,leakage,denominator)]
        gain=[r/l/d for r,l,d in zip(resistance,leakage,denominator)]
        coefficient=inverse_time*(1+rs*p.core_loss_s)+rs*sum((1-g)/l for g,l in zip(gain,leakage))
        right=complex(voltage)-rs*sum((old_s-r-o)/l for r,o,l in zip(old_r,offset,leakage))
        for inner in range(24):
            iterations+=1;magnetic,jac=gradient(old_s,old_s+ds,p.unsaturated_h,p.saturation_beta_per_vs,p.saturation_exponent)
            residual=coefficient*ds+rs*magnetic-right
            if abs(residual)<1e-10:break
            a=coefficient.real+rs*jac[0];b=-coefficient.imag+rs*jac[1];c=coefficient.imag+rs*jac[2];d=coefficient.real+rs*jac[3];det=a*d-b*c
            if not math.isfinite(det) or det<=0:raise RuntimeError('prescribed-voltage Jacobian lost positive invertibility')
            correction=complex((d*residual.real-b*residual.imag)/det,(-c*residual.real+a*residual.imag)/det)
            for reduction in range(24):
                trial=ds-correction*2.**-reduction
                candidate,_=gradient(old_s,old_s+trial,p.unsaturated_h,p.saturation_beta_per_vs,p.saturation_exponent)
                if abs(coefficient*trial+rs*candidate-right)<abs(residual):ds=trial;break
            else:raise RuntimeError('prescribed-voltage Newton line search failed')
        else:raise RuntimeError('prescribed-voltage Newton did not converge')
        dr=[o+g*ds for o,g in zip(offset,gain)]
        midpoint_s=old_s+ds;midpoint_r=[r+delta for r,delta in zip(old_r,dr)]
        rotor_current=[(r-midpoint_s)/l for r,l in zip(midpoint_r,leakage)]
        magnetic_current=magnetic-sum(rotor_current);current=magnetic_current+p.core_loss_s*inverse_time*ds
        torque=1.5*p.pole_pairs*(magnetic_current*midpoint_s.conjugate()).imag
        load=load_torque_nm+p.fan_nm_per_rad_s2*wmid*abs(wmid)+p.viscous_nm_per_rad_s*wmid
        next_mid=speed if p.locked_rotor else speed+h/(2*p.inertia_kg_m2)*(torque-load)
        if abs(next_mid-wmid)<1e-11:break
        wmid=next_mid
    else:raise RuntimeError('prescribed-voltage shaft iteration did not converge')
    new=[old_s+2*ds,*[r+2*delta for r,delta in zip(old_r,dr)]]
    copper_s=1.5*rs*abs(current)**2;copper_r=1.5*sum(r*abs(i)**2 for r,i in zip(resistance,rotor_current));core=1.5*p.core_loss_s*abs(inverse_time*ds)**2
    error=abs(inverse_time*ds+rs*current-voltage)
    if error>1e-8:raise RuntimeError('prescribed-voltage constitutive residual exceeded bound')
    return dict(flux=np.array([[z.real,z.imag] for z in new]).ravel(),speed_rad_s=float(2*next_mid-speed),speed_mid_rad_s=float(wmid),current_ab=current,rotor_currents_ab=rotor_current,torque_nm=float(torque),load_nm=float(load),stator_copper_w=float(copper_s),rotor_copper_w=float(copper_r),core_loss_w=float(core),source_w=float(1.5*(voltage*current.conjugate()).real),constitutive_residual_v=float(error),newton_iterations=iterations)
