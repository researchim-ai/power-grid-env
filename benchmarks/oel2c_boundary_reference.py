"""Independent finite guard witnesses: bracketed roots, not production selectors."""
import math
from scipy.optimize import brentq


def probe_points(p,input0):
    center=p.KScale*input0;r=p.equilibrium_probe_radius_pu
    lo=max(0.,center-r);hi=center+r
    if not math.isfinite(hi) or lo==hi:raise ValueError('native boundary interval is not representable')
    values={lo,hi,center}
    def add(x):
        if math.isfinite(x) and lo<=x<=hi:
            values.add(float(x))
            for toward in [math.inf,-math.inf]:
                v=math.nextafter(float(x),toward)
                if lo<=v<=hi:values.add(v)
    add(p.ITfPu)
    partitions=sorted({lo,hi,*([p.ITfPu] if lo<p.ITfPu<hi else [])})
    inv=lambda m:p.K2*((m/p.ITfPu)**p.C2-1)
    functions=[lambda m,b=b:inv(m)-b for b in [p.VInvMinPu,p.VInvMaxPu]]
    for offset in [p.FixedRu,p.FixedRd]:
        for level in [p.tMin,p.tMax,p.tFcl,p.tFcl*(1-p.Kzru),p.tFcl-p.Kzru*p.tFcl]:
            functions.append(lambda m,o=offset,l=level:min(p.VInvMaxPu,max(p.VInvMinPu,inv(m)))+o-p.KFb*l)
    def source_release(m):
        offset=p.FixedRu if m<=p.ITfPu else p.FixedRd
        timer=min(p.tMax,max(p.tMin,(min(p.VInvMaxPu,max(p.VInvMinPu,inv(m)))+offset)/p.KFb))
        return (p.tFcl-timer)-p.Kzru*p.tFcl
    functions.append(source_release)
    refs=[p.ILimPu,p.IInstPu]
    if p.equilibrium_reference_current_pu is not None:refs.append(p.equilibrium_reference_current_pu)
    for ref in refs:
        functions.extend([lambda m,v=ref+p.current_comparison_tolerance_pu:p.KAct*m-v,lambda m,v=ref-p.IThOffPu-p.current_comparison_tolerance_pu:p.KAct*m-v])
        for bias in [0.,p.IResetPu]:
            for e in [-1e-10,0.,1e-10]:functions.append(lambda m,v=ref+bias-e:p.KAct*m-v)
    for f in functions:
        for a,b in zip(partitions,partitions[1:]):
            fa,fb=f(a),f(b)
            if not math.isfinite(fa) or not math.isfinite(fb):raise ValueError('nonfinite native guard witness')
            if fa==0:add(a)
            if fb==0:add(b)
            if (fa<0<fb) or (fb<0<fa):add(brentq(f,a,b,xtol=5e-324,rtol=4*math.ulp(1.),maxiter=100))
    ordered=sorted(values)
    for a,b in zip(ordered,ordered[1:]):
        mid=a+(b-a)/2
        if a<mid<b:values.add(mid)
    answer=sorted({v/p.KScale for v in values}|{float(input0)})
    if not all(math.isfinite(v) for v in answer):raise ValueError('native boundary input probes are not finite')
    if len(answer)>512:raise ValueError('native guard witness budget exceeded')
    return answer


def check_neighborhood(p,input0):
    from benchmarks.oel2c_selected_reference import _candidate
    rows=[];modes=[]
    for point in probe_points(p,input0):
        try:
            states,origin=_candidate(p,point)
            if not all(math.isfinite(v) for v in states.values()):raise ValueError('nonfinite native witness state')
            m=states['limiter.firstOrder.y'];q=states['limiter.oelReferenceCurrent.limIntegrator.y'];ref=states['limiter.firstOrder1.y']
            hot=q>=p.tFcl;released=(p.tFcl-q)>=p.Kzru*p.tFcl
            active=hot or released
            rate=p.K1*((m/p.ITfPu)**p.C1-1) if p.Sw1 and active else (p.Kru if released else p.Krd if hot else 0.) if not p.Sw1 else 0.
            enable=p.KAct*m>ref+p.current_comparison_tolerance_pu;reset=ref>p.KAct*m+p.IThOffPu+p.current_comparison_tolerance_pu
            cap=ref>=p.IInstPu-p.cap_tolerance_pu
            bias=bool(not hot and not enable and reset and cap and p.tEn and p.IResetPu)
            error=ref-p.KAct*m+(p.IResetPu if bias else 0.)
            mode=dict(timer_offset=p.FixedRu if m<=p.ITfPu else p.FixedRd,
                reference_dynamics=['inverse',p.K1] if p.Sw1 and p.K1!=0 and active else ['constant',float(rate)],
                timer_bound=-1 if q==p.tMin else 1 if q==p.tMax else 0,reference_bound=-1 if ref==p.ILimPu else 1 if ref==p.IInstPu else 0,
                enable_active=bool(enable),reset_active=bool(reset),upper_cap=bool(cap),bias_enabled=bias,
                reference_selection='explicit_zero_ramp_memory' if rate==0 else 'outward_upper_cap' if rate>0 else 'outward_lower_cap',
                PID_selection='explicit_zero_error_memory' if abs(error)<=1e-10 else 'upper_antiwindup' if error>0 else 'lower_antiwindup')
            raw=p.KpOel*error+states['limiter.pid.I.y']+p.KdOel/p.tDOel*error-states['limiter.pid.D.x']
            output=min(0.,max(p.VOel3MinPu,raw));pid_rate=p.KiOel*(error+(output-raw)/.9)
            if not math.isfinite(pid_rate) or abs(pid_rate)>1e-8:raise ValueError('native analytic probe PID residual exceeds stationarity limit')
            other=mode.copy();alternative=q<=p.tFcl*(1-p.Kzru);other_active=hot or alternative
            alt_rate=(p.K1*((m/p.ITfPu)**p.C1-1) if p.Sw1 else p.Kru if alternative else p.Krd) if other_active else 0.
            other['reference_dynamics']=['inverse',p.K1] if p.Sw1 and p.K1!=0 and other_active else ['constant',float(alt_rate)]
            other['reference_selection']='explicit_zero_ramp_memory' if alt_rate==0 else 'outward_upper_cap' if alt_rate>0 else 'outward_lower_cap'
            for item in [mode,other]:
                if item not in modes:modes.append(item)
            rows.append(dict(input_current_pu=point,passed=mode==other,signature=mode,alternative_arithmetic_signature=other,source_thermal_high=bool(released),rearranged_thermal_high=bool(alternative)))
        except (ValueError,ArithmeticError) as e:rows.append(dict(input_current_pu=point,passed=False,reason=str(e)))
    return dict(passed=all(r['passed'] for r in rows) and len(modes)==1,probes=len(rows),distinct_effective_modes=len(modes),witnesses=rows,
        source_arithmetic_checked=True,coverage='independently bracketed guard roots, adjacent floats and midpoints',interval_certified=False,hybrid_stability_qualified=False)
