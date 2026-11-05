"""Opt-in atomic batching of independent OEL2C below-current clock guards.

Only an already localized below-branch root can seed this batch. Its peers
must be on the same branch, at the existing shifted guard within a machine
roundoff band, moving through it in the required direction. No elapsed-time
window, ODE tolerance, state reset or cross-device physical command is used.
"""
import numpy as np

POLICY='independent_below_clock_batch_v1'


def coalesce_below_clocks(bank,roots,state,time=0.):
    roots=set(roots)
    if len(bank.entries)<2 or not all(p.coincidence_policy==POLICY for _,p,_ in bank.entries.values()):return roots
    seeds=[i for tag,i,name in roots if tag=='oel2c' and name=='branch_below']
    if not seeds:return roots
    modes={bool(bank.entries[i][2].context['below']) for i in seeds}
    if len(modes)!=1:return roots
    mode=modes.pop();eps=64*np.finfo(float).eps;inputs=None
    for i,(a,p,k) in bank.entries.items():
        if bool(k.context['below'])!=mode:continue
        x=state[a:a+8];criterion=k.criteria(x)['below']
        shifted=criterion+(eps if mode else -eps)
        band=eps*max(1.,abs(x[2]),abs(p.KAct*x[0]),p.IThOffPu)
        if abs(shifted)>band:continue
        # The derivative of this guard depends only on its own two states;
        # clock changes leave all continuous states and outputs untouched.
        q=k.quantities(x)
        reference_rate=0. if k.context['reference_bound'] else q['ramp']
        # x0' requires the physical field input; do not infer it from a guard.
        if inputs is None:
            v=bank.model.electrical(state)[0];ig=bank.model.generator_currents(state,v)
            inputs=bank.inputs(state,ig)
        input_pu=inputs[i]
        rate=reference_rate-p.KAct*(p.KScale*input_pu-x[0])/p.tROel
        if not (rate<0. if mode else rate>0.):continue
        # A shallow guard can be roundoff-close in state yet seconds away
        # in time. Require its local projected clock distance to be roundoff
        # close too; no solver rtol/atol or engineering time window is used.
        if abs(shifted/rate)>eps*max(1.,abs(time)):continue
        roots.add(('oel2c',i,'branch_below'))
    return roots
