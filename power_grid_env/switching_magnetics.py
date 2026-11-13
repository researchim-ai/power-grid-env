"""Multicage Gamma winding port with radial magnetic discrete gradient.

The radial divided difference is evaluated as a finite power sum, avoiding
subtraction of nearly equal energies. Iron-loss current is Gc*dPsi_s/dt;
it contributes to terminal current and heat, never to electromagnetic torque.
"""
import math
import itertools
import numpy as np
from power_grid_env.pwm_bridge import T,AB


class SwitchingMagnetics:
    def __init__(self,passport):
        self.p=passport;self.n=1+len(passport.cages);self.size=2*self.n
        self.leakage=np.array([c.leakage_h for c in passport.cages]);self.rr=np.array([c.resistance_ohm for c in passport.cages])
        scalar=np.zeros((self.n,self.n));scalar[0,0]=1/passport.unsaturated_h+sum(1/self.leakage)
        for k,l in enumerate(self.leakage,1):scalar[0,k]=scalar[k,0]=-1/l;scalar[k,k]=1/l
        self.eye2=np.eye(2);self.identity=np.eye(self.size)
        self.spin_unit=np.array([[0.,-1.],[1.,0.]])
        self.input_matrix=np.vstack([self.eye2,np.zeros((self.size-2,2))])
        self.linear_reluctance=np.kron(scalar,self.eye2)

    def resistances(self,temperature=None):
        p=self.p
        if p.thermal is None:return p.stator_r_ohm,self.rr
        h=p.thermal;temperature=np.full(2,h.ambient_c) if temperature is None else np.asarray(temperature)
        factor=1+h.alpha_per_c*(temperature-h.reference_c)
        if not np.all(np.isfinite(factor)) or min(factor)<=0:raise ValueError('nonpositive or nonfinite winding resistance')
        return p.stator_r_ohm*factor[0],self.rr*factor[1]

    def radial_gradient(self,old,mid):
        p=self.p
        if not p.saturation_beta_per_vs:
            coefficient=.5/p.unsaturated_h
            return 2*mid*coefficient,2*coefficient*self.eye2
        new=2*mid-old;a=math.hypot(old[0],old[1]);b=math.hypot(new[0],new[1]);power=p.saturation_exponent+2
        coefficient=.5/p.unsaturated_h;gradient_coefficient=np.zeros(2)
        if p.saturation_beta_per_vs and a+b>0:
            # (b**power-a**power)/(b*b-a*a), with its equal-radius limit.
            total=sum(b**(power-1-k)*a**k for k in range(power))
            derivative=sum((power-1-k)*b**(power-2-k)*a**k for k in range(power-1))
            scale=p.saturation_beta_per_vs**p.saturation_exponent/(power*p.unsaturated_h)
            coefficient+=scale*total/(a+b)
            if b>0:gradient_coefficient=scale*(derivative*(a+b)-total)/(a+b)**2*(2*new/b)
        value=2*mid*coefficient
        jac=2*coefficient*self.eye2+2*np.outer(mid,gradient_coefficient)
        return value,jac

    def energy(self,flux):
        p=self.p;z=np.asarray(flux).reshape(self.n,2);radius=float(np.linalg.norm(z[0]));ir=(z[1:]-z[0])/self.leakage[:,None]
        result=1.5/p.unsaturated_h*(radius**2/2+p.saturation_beta_per_vs**p.saturation_exponent*radius**(p.saturation_exponent+2)/(p.saturation_exponent+2))+.75*sum(self.leakage*np.sum(ir*ir,axis=1))
        return float(result)

    def magnetic_currents(self,flux):
        p=self.p;z=np.asarray(flux).reshape(self.n,2);ir=(z[1:]-z[0])/self.leakage[:,None]
        force=z[0]*(1+(p.saturation_beta_per_vs*np.linalg.norm(z[0]))**p.saturation_exponent)/p.unsaturated_h-sum(ir)
        return np.concatenate((force,ir.ravel()))

    def affine(self,old,h,speed,guess,temperature=None):
        p=self.p;rs,rr=self.resistances(temperature);matrix=self.linear_reluctance.copy();gradient,jac=self.radial_gradient(old[:2],guess[:2]);matrix[:2,:2]+=jac-self.eye2/p.unsaturated_h
        offset=np.zeros(self.size);offset[:2]=gradient-jac@guess[:2]
        # Current-coordinate gradient rows, plus explicit core feedthrough
        # expressed in flux increments. This yields a Norton voltage port.
        physical=matrix.copy();physical[:2,:2]+=p.core_loss_s*2/h*self.eye2
        physical_offset=offset.copy();physical_offset[:2]-=p.core_loss_s*2/h*old[:2]
        resistance=np.repeat(np.concatenate(([rs],rr)),2)
        left=2/h*self.identity+resistance[:,None]*physical
        spin=self.spin_unit*p.pole_pairs*speed
        for k in range(1,self.n):left[2*k:2*k+2,2*k:2*k+2]-=spin
        old_current=matrix@old+offset
        rhs=-resistance*old_current
        for k in range(1,self.n):rhs[2*k:2*k+2]+=spin@old[2*k:2*k+2]
        B=self.input_matrix
        solved=np.linalg.solve(left,np.column_stack([rhs,B]));offset_delta=solved[:,0];offset_flux=old+offset_delta;gain_flux=solved[:,1:]
        motor_offset=old_current[:2]+physical[:2]@offset_delta;motor_gain=physical[:2]@gain_flux
        return dict(offset_delta=offset_delta,offset_flux=offset_flux,gain_flux=gain_flux,motor_offset=motor_offset,motor_gain=motor_gain,
                    physical_matrix=physical,physical_offset=physical_offset,gradient=gradient,jacobian=jac,guess=guess.copy())

    def stage(self,old,mid,h,voltage,increment=None,temperature=None):
        p=self.p;rs,rr=self.resistances(temperature);z=np.asarray(mid).reshape(self.n,2);ir=(z[1:]-z[0])/self.leakage[:,None]
        gradient,_=self.radial_gradient(old[:2],mid[:2]);force=gradient-sum(ir);ds=2/h*((mid[:2]-old[:2]) if increment is None else increment[:2]);current=force+p.core_loss_s*ds
        expected=ds+rs*current
        torque=1.5*p.pole_pairs*(force[1]*z[0,0]-force[0]*z[0,1])
        stator_copper=1.5*rs*(current@current);rotor_copper=1.5*sum(rr*np.sum(ir*ir,axis=1));copper=stator_copper+rotor_copper
        core=1.5*p.core_loss_s*(ds@ds)
        return dict(currents=np.concatenate((current,ir.ravel())),torque_nm=float(torque),copper_w=float(copper),stator_copper_w=float(stator_copper),rotor_copper_w=float(rotor_copper),stator_resistance_ohm=float(rs),rotor_resistances_ohm=rr.tolist(),core_w=float(core),constitutive_residual_v=float(max(abs(expected-voltage))))

    def terminal_current(self,flux,rails,gates,channel_r,diode_r,drop,temperature=None):
        """Instantaneous core-feedthrough solve at fixed flux and DC rails.

No current from the preceding interval is reused after a gate or voltage jump.
Only three motor pole potentials are solved; their isolated common mode is a
free gauge when all diodes and channels are blocked.
"""
        magnetic=self.magnetic_currents(flux)
        if not self.p.core_loss_s:return magnetic
        rs,_=self.resistances(temperature)
        denominator=1+rs*self.p.core_loss_s
        conductance=self.p.core_loss_s/denominator;offset=magnetic[:2]/denominator
        base=conductance*T@AB;rhs=-T@offset
        for i in range(3):
            if gates[i,0]:base[i,i]+=1/channel_r;rhs[i]+=rails[0]/channel_r
            if gates[i,1]:base[i,i]+=1/channel_r;rhs[i]+=rails[1]/channel_r
        voltage=np.full(3,float(np.mean(rails)))
        for _ in range(30):
            upper=np.maximum(0.,(voltage-rails[0]-drop)/diode_r);lower=np.maximum(0.,(rails[1]-voltage-drop)/diode_r)
            residual=base@voltage-rhs+upper-lower
            if max(abs(residual))<1e-9:
                result=magnetic.copy();result[:2]=offset+conductance*(AB@voltage);return result
            jac=base+np.diag(((voltage>rails[0]+drop)|(voltage<rails[1]-drop))/diode_r)
            delta=np.linalg.lstsq(jac,-residual,rcond=1e-14)[0]
            # The port is a passive resistive network: use its exact quadratic
            # plus hinge potential difference to cross commutation boundaries.
            for factor in [2.**-i for i in range(28)]:
                change=factor*delta;trial=voltage+change
                u=np.maximum(trial-rails[0]-drop,0.);l=np.maximum(rails[1]-trial-drop,0.)
                difference=(base@voltage-rhs)@change+.5*change@base@change+.5*sum((u-upper*diode_r)*(u+upper*diode_r)+(l-lower*diode_r)*(l+lower*diode_r))/diode_r
                new=base@trial-rhs+(u-l)/diode_r
                if max(abs(new))<max(abs(residual)) or difference < -1e-14:
                    voltage=trial;break
            else:break
        # At a diode hinge the descent method can stall in the isolated-star
        # common-mode nullspace. There are only 3**3 passive diode states;
        # enumerate them without perturbing flux, rails, gates or tolerances.
        for status in itertools.product((-1,0,1),repeat=3):
            state=np.asarray(status);on=state!=0
            if not np.any(gates) and not np.any(on):
                trial=-T@offset/conductance
                lo=max(rails[1]-drop-trial);hi=min(rails[0]+drop-trial)
                if lo>hi:continue
                trial+=min(hi,max(lo,float(np.mean(rails))))
            else:
                matrix=base+np.diag(on/diode_r)
                forcing=rhs+np.where(state>0,(rails[0]+drop)/diode_r,np.where(state<0,(rails[1]-drop)/diode_r,0.))
                trial=np.linalg.solve(matrix,forcing)
            upper=trial-rails[0]-drop;lower=rails[1]-trial-drop
            if np.any((state==0)&((upper>1e-10)|(lower>1e-10))) or np.any((state==1)&(upper < -1e-10)) or np.any((state==-1)&(lower < -1e-10)):continue
            residual=base@trial-rhs+(np.maximum(upper,0.)-np.maximum(lower,0.))/diode_r
            if not np.all(np.isfinite(trial)) or max(abs(residual))>=1e-9:continue
            result=magnetic.copy();result[:2]=offset+conductance*(AB@trial);return result
        raise RuntimeError('instantaneous core-current passive active-set solve failed')
