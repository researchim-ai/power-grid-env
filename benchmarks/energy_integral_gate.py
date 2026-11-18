"""Energy gates split every hybrid boundary; two-point segments use trapezoids."""
import numpy as np
from scipy.integrate import quad,simpson

REFERENCE_GATE_J=1e-4
NATIVE_GATE_J=.1


def reference_error(ref):
    def energy(t):
        q=ref.sample(t);return q['g.energy.kinetic_energy_j']+q['g.energy.thermal_energy_j']
    def power(t):
        q=ref.sample(t);return q['g.energy.shaft_power_w']-q['g.energy.terminal_power_w']-q['g.energy.signed_damping_exchange_w']-q['g.energy.ambient_heat_w']
    cuts=sorted({0.,ref.w.s.duration_s,*[e['time_s'] for e in ref.events]})
    integral=sum(quad(power,left,right,epsabs=1e-5)[0] for left,right in zip(cuts,cuts[1:]))
    error=float(abs(energy(cuts[-1])-energy(0)-integral))
    if not np.isfinite(error) or error>REFERENCE_GATE_J:raise ValueError('independent integral energy balance failed')
    return error


def native_error(report):
    channels=report['channels'];times=np.array(report['time_s']);sides=report['sample_side']
    cuts=sorted({0.,times[-1],*[e['time_s'] for e in report['event_log']]})
    power=np.array(channels['g.energy.shaft_power_w'])-channels['g.energy.terminal_power_w']-np.array(channels['g.energy.signed_damping_exchange_w'])-channels['g.energy.ambient_heat_w']
    integral=0.
    for left,right in zip(cuts,cuts[1:]):
        indices=[j for j,t in enumerate(times) if left<t<right or (t==left and sides[j] in ('initial','post_event')) or (t==right and sides[j] in ('regular','pre_event'))]
        tt=times[indices]
        if len(tt)<2 or np.any(np.diff(tt)<=0):raise ValueError('invalid energy quadrature axis')
        integral+=(power[indices[0]]+power[indices[1]])*(tt[1]-tt[0])/2 if len(tt)==2 else simpson(power[indices],x=tt)
    stored=np.array(channels['g.energy.kinetic_energy_j'])+channels['g.energy.thermal_energy_j'];error=float(abs(stored[-1]-stored[0]-integral))
    if not np.isfinite(error) or error>NATIVE_GATE_J or max(abs(v) for v in channels['g.energy.balance_residual_w'])>1e-7:raise ValueError('native integral energy balance failed')
    return error
