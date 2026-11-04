# SPDX-License-Identifier: MPL-2.0
# Stationary guard analysis for Dynawo Oel2c; see assets/licenses/Dynawo-MPL-2.0.txt.
"""Finite critical-point witnesses, never an interval or stability certificate."""
import math
import numpy as np


class SensitiveEquilibrium(ValueError):
    def __init__(self,audit):
        self.audit=audit
        super().__init__('sensitive OEL2C stationary neighborhood; inspect boundary_audit witnesses')


def probe_points(p,input_pu):
    """Input-current grid with analytic guard candidates and both floating sides."""
    center=float(p.KScale*input_pu);radius=p.equilibrium_probe_radius_pu
    lo=max(0.,center-radius);hi=center+radius
    if not math.isfinite(hi) or hi==lo:raise ValueError('finite representable boundary probe interval required')
    seeds={lo,center,hi}
    def include(value):
        if math.isfinite(value) and lo<=value<=hi:
            seeds.add(float(value))
            for target in [-math.inf,math.inf]:
                neighbor=math.nextafter(float(value),target)
                if lo<=neighbor<=hi:seeds.add(neighbor)
    include(p.ITfPu)
    targets={p.VInvMinPu,p.VInvMaxPu}
    for level in [p.tMin,p.tMax,p.tFcl,p.tFcl*(1-p.Kzru),p.tFcl-p.Kzru*p.tFcl]:
        for offset in [p.FixedRu,p.FixedRd]:targets.add(p.KFb*level-offset)
    for value in targets:
        base=1+value/p.K2
        if base>=0:
            try:include(p.ITfPu*base**(1/p.C2))
            except OverflowError:pass
    refs={p.ILimPu,p.IInstPu}
    if p.equilibrium_reference_current_pu is not None:refs.add(p.equilibrium_reference_current_pu)
    for ref in refs:
        include((ref+p.current_comparison_tolerance_pu)/p.KAct)
        include((ref-p.IThOffPu-p.current_comparison_tolerance_pu)/p.KAct)
        for bias in [0.,p.IResetPu]:
            for error in [-1e-10,0.,1e-10]:include((ref+bias-error)/p.KAct)
    ordered=sorted(seeds)
    for a,b in zip(ordered,ordered[1:]):
        mid=a+(b-a)/2
        if a<mid<b:seeds.add(mid)
    points=sorted({v/p.KScale for v in seeds}|{float(input_pu)})
    if not all(math.isfinite(v) for v in points):raise ValueError('boundary input probes are not finite')
    if len(points)>512:raise ValueError('boundary probe budget exceeded')
    return points


def signature(p,x,c,audit):
    # Structural equivalence: scaled_low is immaterial for identical offsets;
    # thermal flags are immaterial when they select identical ramp and bias.
    active=c['thermal_high'] or c['thermal_low']
    rate=(p.K1*((x[0]/p.ITfPu)**p.C1-1) if p.Sw1 else p.Kru if c['thermal_high'] else p.Krd) if active else 0.
    ramp=['inverse',p.K1] if p.Sw1 and p.K1!=0 and active else ['constant',float(rate)]
    if ramp[0]=='constant' and ramp[1]==0:ramp=['constant',0.]
    return dict(timer_offset=float(p.FixedRu if c['scaled_low'] else p.FixedRd),
        reference_dynamics=ramp,timer_bound=int(c['timer_bound']),reference_bound=int(c['reference_bound']),
        enable_active=bool(c['above']),reset_active=bool(c['below']),upper_cap=bool(c['upper_cap']),
        bias_enabled=bool(not c['thermal_low'] and not c['above'] and c['below'] and c['upper_cap'] and p.tEn!=0 and p.IResetPu!=0),
        reference_selection='explicit_zero_ramp_memory' if rate==0 else 'outward_upper_cap' if rate>0 else 'outward_lower_cap',PID_selection=audit['pid_selection'])


def check_neighborhood(p,input_pu,candidate):
    from power_grid_env.generator_oel2c import OEL2CDynamics
    points=probe_points(p,input_pu);rows=[];signatures=[]
    x,c,a=candidate(p,input_pu);central=OEL2CDynamics(p);central.context=c;q=central.quantities(x)
    raw2=p.tC2Oel/p.tB2Oel*(q['pid']-x[6]);raw1=p.tC1Oel/p.tB1Oel*(q['lead2']-x[7])
    distances=central.criteria(x)|dict(rearranged_thermal_release=p.tFcl*(1-p.Kzru)-x[1],timer_lower=x[1]-p.tMin,timer_upper=p.tMax-x[1],reference_lower=x[2]-p.ILimPu,reference_upper=p.IInstPu-x[2],
        PID_lower=q['raw']-p.VOel3MinPu,PID_upper=-q['raw'],leadlag2_lower=raw2-p.VOel2MinPu,leadlag2_upper=-raw2,leadlag1_lower=raw1-p.VOel1MinPu,leadlag1_upper=-raw1)
    scale=max(1.,float(max(abs(x))),p.ITfPu,p.IInstPu,abs(p.tMin),abs(p.tMax))
    surfaces={name:dict(signed_distance=float(value),unit='s' if name in ('thermal_high','thermal_low','rearranged_thermal_release','timer_lower','timer_upper') else 'pu',within_roundoff=bool(abs(value)<=64*np.finfo(float).eps*scale)) for name,value in distances.items()}
    for point in points:
        try:
            x,c,a=candidate(p,point);k=OEL2CDynamics(p);k.context=c
            residual=float(max(abs(k.rhs(x,point))))
            if not math.isfinite(residual) or residual>1e-8:raise ValueError('probe derivative exceeds stationarity limit')
            s=signature(p,x,c,a);alternative=c|dict(thermal_high=bool(x[1]<=p.tFcl*(1-p.Kzru)))
            other=signature(p,x,alternative,a)
            for item in [s,other]:
                if item not in signatures:signatures.append(item)
            rows.append(dict(input_current_pu=point,passed=s==other,signature=s,alternative_arithmetic_signature=other,
                source_thermal_high=bool(c['thermal_high']),rearranged_thermal_high=alternative['thermal_high'],maximum_derivative_per_s=residual))
        except (ValueError,ArithmeticError) as e:rows.append(dict(input_current_pu=point,passed=False,reason=str(e)))
    passed=all(r['passed'] for r in rows) and len(signatures)==1
    return dict(passed=passed,policy='reject_sensitive_neighborhood',radius_measured_current_pu=p.equilibrium_probe_radius_pu,
        probes=len(rows),distinct_effective_modes=len(signatures),central_switching_surfaces=surfaces,witnesses=rows,
        coverage='finite analytic critical points, adjacent floats and interval midpoints',
        source_arithmetic_checked=True,interval_certified=False,hybrid_stability_qualified=False,physical_input_or_threshold_changed=False)
