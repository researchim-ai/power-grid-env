"""Independent SI T-circuit + passive heat-balance stationary root and dynamics.

No native initialization, network elimination, tangent or winding helpers are
used. The reference uses finite-difference bounded search of an independently
written T-circuit/heat system, followed by physical SI flux-state integration.
"""
import numpy as np
from scipy.optimize import least_squares
from benchmarks.rms_winding_reference import ElectrothermalReference


class StationaryReference(ElectrothermalReference):
    def __init__(self,study):
        super().__init__(study)
        if self.study.coupled.operating_point.winding_state!='equilibrium':raise ValueError('stationary winding policy required')
        self.w.initialize=self.stationary_initial

    def stationary_initial(self):
        w,h=self.w,self.h
        minimum=self.study.coupled.operating_point.minimum_speed_pu
        def evaluate(v):
            speed,ts,tr=v
            w.rs=self.rs0*(1+h.alpha_per_c*(ts-h.reference_c));w.rr=self.rr0*(1+h.alpha_per_c*(tr-h.reference_c))
            vd,im,ir,flux,torque=w.static(speed)
            cross=h.cross_w_per_k*(ts-tr)
            hs=3*w.rs*abs(im)**2-h.stator_ambient_w_per_k*(ts-h.ambient_c)-cross
            hr=3*w.rr*abs(ir)**2-h.rotor_ambient_w_per_k*(tr-h.ambient_c)+cross
            return np.array([torque-w.load(speed),hs/w.m.rated_va,hr/w.m.rated_va]),(vd,im,ir,flux,torque)
        lower=[minimum,h.ambient_c,h.ambient_c];upper=[1.,h.maximum_c-1e-7,h.maximum_c-1e-7]
        for speed in [.98,.94,.88]:
            seed=np.clip([speed,*self.initial_temperatures],np.array(lower)+1e-7,np.array(upper)-1e-7)
            solution=least_squares(lambda v:evaluate(v)[0],seed,bounds=(lower,upper),jac='3-point',xtol=1e-14,ftol=1e-14,gtol=1e-14,max_nfev=500)
            residual,(vd,im,ir,flux,torque)=evaluate(solution.x)
            if max(abs(residual))>1e-11:continue
            speed,ts,tr=solution.x;self.initial_temperatures=(float(ts),float(tr))
            state=np.array([flux.real,flux.imag,speed,(abs(im)/w.ib)**2 if w.m.thermal else 0.])
            full=np.r_[state,np.zeros(len(self.relays)),self.initial_temperatures]
            context=dict(on=True,line=True,source=True,faults=set(),relays={r.id:[False,False,r.measurement_available] for r in self.relays})
            f=self.rhs(0.,full,context)
            indices=np.array([0,1,2,3,len(full)-2,len(full)-1]);jac=np.empty((6,6))
            for j,i in enumerate(indices):
                step=1e-6*max(1.,abs(full[i]));plus=full.copy();minus=full.copy();plus[i]+=step;minus[i]-=step
                jac[:,j]=(self.rhs(0.,plus,context)[indices]-self.rhs(0.,minus,context)[indices])/(2*step)
            spectrum=np.linalg.eigvals(jac)
            if max(abs(f[indices]))>1e-8 or max(spectrum.real)>=-1e-9:continue
            return state,dict(mode='independent_T_circuit_heat_equilibrium',speed_pu=float(speed),stator_temperature_c=float(ts),rotor_temperature_c=float(tr),
                current_pu=float(abs(im)/w.ib),voltage_pu=float(abs(vd)/w.vb),maximum_physical_residual_per_s=float(max(abs(f[indices]))),
                eigenvalues_per_s=[[float(z.real),float(z.imag)] for z in spectrum])
        raise ValueError('independent bounded T-circuit heat root not found; not a global nonexistence proof')
