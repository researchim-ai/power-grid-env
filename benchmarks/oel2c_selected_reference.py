"""Independent SI algebraic active-set construction for the native FMU.

No production initialization, RHS or active-set selector is imported.
"""
import math


def _candidate(p,input0):
    clip=lambda value,lo,hi:min(hi,max(lo,value))
    m=input0*p.KScale;a=p.KAct*m
    if not math.isfinite(m) or m<0:raise ValueError('invalid native selected current')
    inverse=clip(p.K2*((m/p.ITfPu)**p.C2-1),p.VInvMinPu,p.VInvMaxPu)
    q=clip((inverse+(p.FixedRu if m<=p.ITfPu else p.FixedRd))/p.KFb,p.tMin,p.tMax)
    warm=q>=p.tFcl;released=(p.tFcl-q)>=p.Kzru*p.tFcl
    rate=(p.K1*((m/p.ITfPu)**p.C1-1) if p.Sw1 else p.Kru if released else p.Krd) if released or warm else 0.
    ref=p.equilibrium_reference_current_pu
    if rate:
        forced=p.IInstPu if rate>0 else p.ILimPu
        if ref is not None and ref!=forced:raise ValueError('native reference memory contradicts free ramp')
        ref=forced
    elif ref is None:raise ValueError('native zero-ramp reference memory is required')
    if not p.ILimPu<=ref<=p.IInstPu:raise ValueError('native reference memory is outside limits')
    band=p.current_comparison_tolerance_pu
    enable=a>ref+band;reset=ref>a+p.IThOffPu+band
    bias=p.IResetPu if not warm and not enable and reset and ref>=p.IInstPu-p.cap_tolerance_pu and p.tEn else 0.
    e=ref-a+bias
    u=p.equilibrium_pid_output_pu
    if abs(e)<=1e-10:
        if u is None:raise ValueError('native zero-error PID memory is required')
        integral=u-p.KpOel*e
    else:
        forced=0. if e>0 else p.VOel3MinPu
        if u is not None and u!=forced:raise ValueError('native PID memory contradicts antiwindup')
        u=forced;integral=u+(.9-p.KpOel)*e
    if not p.VOel3MinPu<=u<=0:raise ValueError('native PID memory is outside limits')
    v=clip(u,p.VOel2MinPu,0.);out=clip(v,p.VOel1MinPu,0.)
    states={'limiter.firstOrder.y':m,'limiter.firstOrder1.y':ref,
        'limiter.oelReferenceCurrent.limIntegrator.y':q,'limiter.oelReferenceCurrent.limIntegrator1.y':ref,
        'limiter.pid.I.y':integral,'limiter.pid.D.x':p.KdOel/p.tDOel*e,
        'limiter.limitedLeadLag.firstOrder.y':v*(p.tC2Oel-p.tB2Oel)/p.tC2Oel,
        'limiter.limitedLeadLag1.firstOrder.y':out*(p.tC1Oel-p.tB1Oel)/p.tC1Oel}
    return states,-(p.tEn+1.) if enable else -(p.tOff+1.) if reset else 0.


def initial_conditions(p,input0):
    answer=_candidate(p,input0)
    if p.equilibrium_boundary_policy=='reject_sensitive_neighborhood':
        from benchmarks.oel2c_boundary_reference import check_neighborhood
        if not check_neighborhood(p,input0)['passed']:raise ValueError('native sensitive stationary neighborhood')
    return answer
