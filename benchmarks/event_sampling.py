"""Reference sampling distinguishes continuous limiter roots from circuit jumps."""
from collections import defaultdict


def native_sample_sides(report):
    events=defaultdict(list)
    for event in report.get('event_log',[]):events[event['time_s']].append(event['kind'])
    result=[]
    for at,side in zip(report['time_s'],report['sample_side']):
        kinds=events.get(at,[])
        continuous=bool(kinds) and all(k.startswith('controller_limit_') for k in kinds)
        result.append('regular' if continuous and side in ('pre_event','post_event') else side)
    return result


def native_sample_indices(report,step_s):
    """Native clocks without extra query markers compare their declared grid.

    Pure derivative-limit roots between export samples are checked by full SI
    and analytic references, not by an inaccurate chord of a fast rotating EMF.
    Every physical event side and every original grid point is retained.
    """
    import math
    if not math.isfinite(step_s) or step_s<=0:raise ValueError('positive finite native output step required')
    semantic=native_sample_sides(report);indices=[]
    for j,(at,side,kind) in enumerate(zip(report['time_s'],report['sample_side'],semantic)):
        extra=side in ('pre_event','post_event') and kind=='regular'
        on_grid=abs(at-round(at/step_s)*step_s)<=64*2.220446049250313e-16*max(1.,abs(at))
        if not extra or on_grid:indices.append(j)
    return indices
