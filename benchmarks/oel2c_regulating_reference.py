"""Independent initial algebraic data for the native interior PID branch."""
import math
import numpy as np


def initial_conditions(p,input0,output):
    m=p.KScale*input0;actual=p.KAct*m
    eps=128*np.finfo(float).eps*max(1.,abs(m),p.ITfPu,p.ILimPu)
    if output is None or not all(math.isfinite(v) for v in [m,actual,output]) or m<=p.ITfPu+eps or abs(actual-p.ILimPu)>min(1e-9,p.current_comparison_tolerance_pu/4):raise ValueError('native regulating field-current equality is not satisfied')
    inverse=min(max(p.K2*((m/p.ITfPu)**p.C2-1),p.VInvMinPu),p.VInvMaxPu)
    timer=min(max((inverse+p.FixedRd)/p.KFb,p.tMin),p.tMax)
    ramp=p.K1*((m/p.ITfPu)**p.C1-1) if p.Sw1 else p.Krd
    if timer<=p.tFcl+eps or ramp>=0:raise ValueError('native regulating hot branch is infeasible')
    if not max(p.VOel1MinPu,p.VOel2MinPu,p.VOel3MinPu)+1e-8<output<-1e-8:raise ValueError('native specified reference requires non-interior OEL authority')
    if timer==p.tMax and inverse+p.FixedRd-p.KFb*timer<=eps:raise ValueError('native timer bound has no strict outward margin')
    e=p.ILimPu-actual
    states={'limiter.firstOrder.y':m,'limiter.firstOrder1.y':p.ILimPu,
        'limiter.oelReferenceCurrent.limIntegrator.y':timer,'limiter.oelReferenceCurrent.limIntegrator1.y':p.ILimPu,
        'limiter.pid.I.y':output-p.KpOel*e,'limiter.pid.D.x':p.KdOel/p.tDOel*e,
        'limiter.limitedLeadLag.firstOrder.y':(1-p.tB2Oel/p.tC2Oel)*output,
        'limiter.limitedLeadLag1.firstOrder.y':(1-p.tB1Oel/p.tC1Oel)*output}
    return states,0.
