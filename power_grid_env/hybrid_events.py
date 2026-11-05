"""Independently located roundoff-time cohorts and transversal jump products."""
import numpy as np
from scipy.optimize import brentq

PHASE_ORDER=('motor_thermal','generator_stator_thermal','generator_field_thermal','ESST1A','OEL2C','UEL','controller_bounds','scheduled_nonmotor','manual_motor','automatic_contactors')


def coincident_roots(solution,guards,*,ulps=64,check_time=None):
    if type(ulps) is not int or not 1<=ulps<=64:raise ValueError('coincidence ULP budget must be in 1..64')
    check_time=(lambda:None) if check_time is None else check_time
    evaluations=0
    def value(guard,t,dense):
        nonlocal evaluations
        check_time();evaluations+=1;return float(guard(t,dense(t)))
    stop=float(solution.t[-1]);base={i for i,r in enumerate(solution.t_events or []) if len(r)}
    tolerance=ulps*np.finfo(float).eps*max(1.,abs(stop));selected=set(base);located={i:stop for i in base}
    if base:
        dense=solution.sol.interpolants[-1];a=float(dense.t_old);b=float(dense.t)
        for i,guard in enumerate(guards):
            if i in base:continue
            ga=value(guard,a,dense);gb=value(guard,b,dense)
            if not np.isfinite([ga,gb]).all():raise ValueError('nonfinite coincidence guard')
            if ga==gb==0:continue  # A flat zero has no independently located crossing.
            direction=getattr(guard,'direction',0)
            rising=ga<=0<=gb;falling=ga>=0>=gb
            if not ((direction>0 and rising) or (direction<0 and falling) or (direction==0 and (rising or falling))):continue
            root=float(brentq(lambda t:value(guard,t,dense),a,b,xtol=4*np.finfo(float).eps,rtol=4*np.finfo(float).eps))
            if abs(root-stop)<=tolerance:selected.add(i);located[i]=root
    return selected,dict(cohort_guard_evaluations=evaluations,time_s=stop,solver_root_indices=sorted(base),cohort_root_indices=sorted(selected),independently_located_times={str(i):t for i,t in located.items()},maximum_snap_s=max([0.,*[abs(t-stop) for t in located.values()]]),time_tolerance_s=tolerance,phase_order=list(PHASE_ORDER),policy='coincident_roots_v1')


def saltation_product(normal,f_before,f_after,direction,*,reset_product=None,reset_time_derivative=None,guard_time_derivative=0.,minimum_transversality=1e-10):
    """Local saltation for one transversal guard and an explicitly supplied reset.

    Simultaneous noncommuting events need an authored composition; this function
    does not infer a gradient for a cohort, grazing contact or an entire study.
    """
    n=np.asarray(normal);fm=np.asarray(f_before);fp=np.asarray(f_after);v=np.asarray(direction)
    if n.ndim!=1 or not len(n) or any(a.shape!=n.shape or not np.isrealobj(a) or not np.isfinite(a).all() for a in [n,fm,fp,v]):raise ValueError('finite equal-sized real saltation vectors required')
    if not np.isfinite([guard_time_derivative,minimum_transversality]).all() or minimum_transversality<=0:raise ValueError('invalid transversality controls')
    denominator=float(n@fm+guard_time_derivative);scale=max(float(np.linalg.norm(n)*np.linalg.norm(fm)),abs(guard_time_derivative),np.finfo(float).tiny)
    if abs(denominator)<=minimum_transversality*scale:raise ValueError('grazing or nontransversal event; saltation not defined')
    reset=(lambda x:x.copy()) if reset_product is None else reset_product
    rv=np.asarray(reset(v));rf=np.asarray(reset(fm));rt=np.zeros_like(fm) if reset_time_derivative is None else np.asarray(reset_time_derivative)
    if any(a.shape!=n.shape or not np.isrealobj(a) or not np.isfinite(a).all() for a in [rv,rf,rt]):raise ValueError('invalid reset derivative')
    result=rv+(fp-rf-rt)*float(n@v)/denominator
    if not np.isfinite(result).all():raise ValueError('nonfinite saltation product')
    return result
