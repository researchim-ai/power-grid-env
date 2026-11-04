"""Independent algebraic data for a compiled, authority-exhausted OEL2C.

No production initialization, derivatives, event clocks or network states are
used. This constructs an initial condition; it does not emulate the regulator.
"""
import math
import numpy as np


def initial_conditions(p,current):
    measured=p.KScale*current;actual=p.KAct*measured
    epsilon=128*np.finfo(float).eps*max(1.,abs(measured),p.ITfPu,p.ILimPu)
    if not math.isfinite(measured) or not math.isfinite(actual) or measured<=p.ITfPu+epsilon or actual<=p.ILimPu+epsilon:
        raise ValueError('native active equilibrium requires strict current overload')
    inverse=min(max(p.K2*((measured/p.ITfPu)**p.C2-1),p.VInvMinPu),p.VInvMaxPu)
    timer=min(max((inverse+p.FixedRd)/p.KFb,p.tMin),p.tMax)
    if timer<=p.tFcl+epsilon:raise ValueError('native active thermal timer is not strictly hot')
    ramp=p.K1*((measured/p.ITfPu)**p.C1-1) if p.Sw1 else p.Krd
    if ramp>=0:raise ValueError('native active equilibrium requires a downward ramp')
    error=p.ILimPu-actual;pid=p.VOel3MinPu
    second=min(max(pid,p.VOel2MinPu),0.);first=min(max(second,p.VOel1MinPu),0.)
    states={
        'limiter.firstOrder.y':measured,'limiter.firstOrder1.y':p.ILimPu,
        'limiter.oelReferenceCurrent.limIntegrator.y':timer,
        'limiter.oelReferenceCurrent.limIntegrator1.y':p.ILimPu,
        'limiter.pid.I.y':pid+(.9-p.KpOel)*error,
        'limiter.pid.D.x':p.KdOel/p.tDOel*error,
        'limiter.limitedLeadLag.firstOrder.y':(1-p.tB2Oel/p.tC2Oel)*second,
        'limiter.limitedLeadLag1.firstOrder.y':(1-p.tB1Oel/p.tC1Oel)*first}
    if not all(math.isfinite(v) for v in states.values()):raise ValueError('nonfinite native active equilibrium')
    return states,-(p.tEn+1.)
