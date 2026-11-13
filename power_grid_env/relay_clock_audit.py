"""Replay continuous relay progress from event-sided sampled RMS measurements.

Three-point Gaussian quadrature of piecewise-linear current interpolants;
exact exponential reset. It audits a saved trace, not an independent machine
trajectory, and does not prove that no extrema lie between samples.
"""
import math

COEFFICIENTS={'standard_inverse':(.14,.02),'very_inverse':(13.5,1.),'extremely_inverse':(80.,2.)}


def rate(current,pickup,curve,multiplier):
    if current<=pickup:return 0.
    if curve=='definite':return 1/multiplier
    k,alpha=COEFFICIENTS[curve]
    return math.expm1(alpha*math.log(current/pickup))/(k*multiplier)


def interval_integral(left,right,dt,pickup,curve,multiplier):
    # Split at pickup so clipping introduces no interior kink in quadrature.
    if dt==0 or max(left,right)<=pickup:return 0.
    if min(left,right)<pickup:
        fraction=(pickup-left)/(right-left)
        if left<pickup:dt*=1-fraction;left=pickup
        else:dt*=fraction;right=pickup
    mid=(left+right)/2;half=(right-left)/2;offset=math.sqrt(3/5)
    return dt/2*sum(w*rate(mid+half*x,pickup,curve,multiplier) for x,w in [(-offset,5/9),(0.,8/9),(offset,5/9)])


def audit_clock(report,relay,*,absolute_limit=2e-4):
    times=report['time_s'];ch=report['channels'];id=relay.id
    keys=['measurement','active','latched','progress','measurement_valid']
    arrays={k:ch[id+'.'+k] for k in keys}
    if len(times)<2 or any(len(v)!=len(times) for v in arrays.values()):raise ValueError('relay clock trace length differs')
    if any(not math.isfinite(v) for v in times) or any(not math.isfinite(v) for a in arrays.values() for v in a):raise ValueError('nonfinite relay trace')
    if any(v<0 for v in arrays['measurement']):raise ValueError('negative RMS measurement')
    if any(v not in [0,1] for k in ['active','latched','measurement_valid'] for v in arrays[k]):raise ValueError('invalid relay discrete state')
    expected=0.;maximum=abs(arrays['progress'][0]);trip_values=[]
    for i in range(1,len(times)):
        dt=times[i]-times[i-1]
        if dt<0:raise ValueError('relay clock time reverses')
        if dt and any(arrays[k][i]!=arrays[k][i-1] for k in ['active','latched','measurement_valid']):
            raise ValueError('relay state change missing event-sided samples')
        if dt and not arrays['latched'][i-1]:
            if arrays['active'][i-1] and arrays['measurement_valid'][i-1]:
                expected+=interval_integral(arrays['measurement'][i-1],arrays['measurement'][i],dt,relay.pickup,relay.curve,relay.time_multiplier_s)
            elif not arrays['active'][i-1]:expected*=math.exp(-dt/relay.reset_s)
        maximum=max(maximum,abs(expected-arrays['progress'][i]))
        if arrays['latched'][i]>arrays['latched'][i-1]:trip_values.append(dict(time_s=times[i],replayed_progress=expected))
        if arrays['latched'][i]<arrays['latched'][i-1]:raise ValueError('relay reset/reclose outside audit v1')
    events=[e for e in report['event_log'] if e['kind']=='network_relay_trip' and e.get('equipment_id')==id]
    coverage=len(events)==len(trip_values) and all(abs(e['time_s']-r['time_s'])<=1e-8 for e,r in zip(events,trip_values))
    crossings=all(abs(r['replayed_progress']-1)<=absolute_limit for r in trip_values)
    result=dict(passed=maximum<=absolute_limit and coverage and crossings,max_progress_error=maximum,absolute_limit=absolute_limit,
                trips=trip_values,event_latch_coverage=coverage,trip_threshold_replayed=crossings,
                scope='quadrature of event-sided interpolated RMS measurements; exact exponential reset, no reclose/reset commands')
    return result
