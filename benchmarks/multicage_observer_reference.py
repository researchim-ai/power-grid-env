"""Independent Cartesian continuous current-model equations, prescribed inputs.

This oracle does not import runtime observer/magnetics/thermal/controller code.
It verifies observer equations, not a complete closed-loop drive trajectory.
"""
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import root


def independent_rhs(passport,current,speed):
    L=np.array([c.leakage_h for c in passport.cages]);R=np.array([c.resistance_ohm for c in passport.cages])
    def rhs(t,flat):
        rotor=flat.reshape(-1,2)
        def residual(stator):
            return stator*(1+(passport.saturation_beta_per_vs*np.linalg.norm(stator))**passport.saturation_exponent)/passport.unsaturated_h+np.sum((stator-rotor)/L[:,None],axis=0)-[current.real,current.imag]
        solution=root(residual,[.1,.1],tol=1e-11)
        if max(abs(residual(solution.x)))>=1e-8:raise RuntimeError('independent constitutive inversion failed')
        return (R[:,None]/L[:,None]*(solution.x-rotor)+passport.pole_pairs*speed*np.column_stack([-rotor[:,1],rotor[:,0]])).ravel()
    return rhs


def reference(passport,current,speed,duration):
    result=solve_ivp(independent_rhs(passport,current,speed),(0,duration),np.zeros(2*len(passport.cages)),method='DOP853',rtol=1e-11,atol=1e-13,max_step=2e-5)
    if not result.success:raise RuntimeError('independent continuous observer failed')
    return result.y[:,-1]
