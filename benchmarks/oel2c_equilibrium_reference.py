"""Independent algebraic initialization of the compiled inactive OEL2C branch.

This module supplies native state names and initial values, never a limiter RHS.
The native block uses time = study time + (tOff + 1 s), aging its reset
clock before FMI event iteration installs the independently solved states.
This is an explicit initial-condition construction, not a simulated prehistory.
"""
import math
import numpy as np


def initial_conditions(p,input0,*,regulating_output_pu=None):
    if p.initialization=='selected_equilibrium':
        from benchmarks.oel2c_selected_reference import initial_conditions as selected
        return selected(p,input0)
    if p.initialization=='current_regulating_equilibrium':
        from benchmarks.oel2c_regulating_reference import initial_conditions as regulating
        return regulating(p,input0,regulating_output_pu)
    if p.initialization=='active_limited_equilibrium':
        from benchmarks.oel2c_active_reference import initial_conditions as active
        return active(p,input0)
    sensed=p.KScale*input0;act=p.KAct*sensed
    eps=128*np.finfo(float).eps*max(1.,abs(sensed),p.ITfPu,p.IInstPu)
    if not math.isfinite(sensed) or sensed<0 or sensed>=p.ITfPu-eps or act>=p.IInstPu-eps:
        raise ValueError('native inactive OEL2C requires strictly subthermal current and cap headroom')
    difference=p.IInstPu-act-p.IThOffPu
    if abs(difference)<=eps:raise ValueError('native reset boundary is not a qualified equilibrium')
    slope=p.K1*((sensed/p.ITfPu)**p.C1-1) if p.Sw1 else p.Kru
    if slope<=0:raise ValueError('native inactive upper-reference branch requires positive ramp')
    inverse=min(max(p.K2*((sensed/p.ITfPu)**p.C2-1),p.VInvMinPu),p.VInvMaxPu)
    thermal=min(max((inverse+p.FixedRu)/p.KFb,p.tMin),p.tMax)
    if thermal>=p.tFcl*(1-p.Kzru)-eps:raise ValueError('native thermal timer is outside the strict inactive release branch')
    bias=p.IResetPu if difference>0 and p.tEn!=0 else 0.
    error=p.IInstPu-act+bias
    states=dict(zip([
        'limiter.firstOrder.y','limiter.firstOrder1.y',
        'limiter.limitedLeadLag.firstOrder.y','limiter.limitedLeadLag1.firstOrder.y',
        'limiter.oelReferenceCurrent.limIntegrator.y','limiter.oelReferenceCurrent.limIntegrator1.y',
        'limiter.pid.D.x','limiter.pid.I.y'],[
        sensed,p.IInstPu,0.,0.,thermal,p.IInstPu,p.KdOel/p.tDOel*error,(.9-p.KpOel)*error]))
    if not all(math.isfinite(v) for v in states.values()):raise ValueError('nonfinite native equilibrium')
    return states,-(p.tOff+1.)
