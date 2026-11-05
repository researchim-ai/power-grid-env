"""Exact continuous OEL2C rows for the current explicit clock/active set.

Context is read-only. Clip ties use zero outward slope; abs(Ifd) uses zero
at Ifd=0. No derivative of reset, deadline or branch switching is asserted.
"""
import numpy as np

KIND='piecewise_analytic_OEL2C_network_reduction'


def supported(model):
    from power_grid_env.protection_jacobian import supported as common
    return bool(model.oel2c.entries) and (len(model.oel2c.entries)==1 or all(p.coincidence_policy=='independent_below_clock_batch_v1' for _,p,_ in model.oel2c.entries.values())) and common(model,allow_oel=True)


def callback(model):
    if not supported(model):return None
    def evaluate(time,state):
        from power_grid_env.genrou_jacobian import jacobian
        return jacobian(model,state,oel=True)
    evaluate.jacobian_kind=KIND
    from power_grid_env.jacobian_columns import attach
    def evaluate_columns(time,state,cols,ctx):
        from power_grid_env.genrou_jacobian import jacobian
        return jacobian(model,state,oel=True,columns=cols,electrical=ctx)
    def actions(time,state,u,ctx,voltage_directions=None):
        from power_grid_env.genrou_jacobian import jacobian
        return jacobian(model,state,oel=True,directions=u,electrical=ctx,voltage_directions=voltage_directions)
    return attach(evaluate,model,evaluate_columns,actions)


def power_slope(value,exponent):
    if value<0 and not float(exponent).is_integer():raise ValueError('fractional OEL2C power at negative measured current')
    if value==0:
        if exponent<1:raise ValueError('OEL2C fractional-power derivative singular at zero current')
        return 1. if exponent==1 else 0.
    return exponent*value**(exponent-1)


def rows(kernel,x,u,dinput):
    """Eight full-coordinate rows and clipped SEXS summation gradient."""
    from power_grid_env.genrou_jacobian import clip_slope
    p=kernel.p;c=kernel.context;q=kernel.quantities(x);scaled=x[0]/p.ITfPu
    raw_inverse=p.K2*(scaled**p.C2-1)
    dinverse=np.zeros(u.shape[1])
    if not c['timer_bound'] and p.VInvMinPu<raw_inverse<p.VInvMaxPu:dinverse=p.K2*power_slope(scaled,p.C2)*u[0]/p.ITfPu
    ramp=np.zeros(u.shape[1])
    if p.Sw1 and not c['reference_bound'] and (c['thermal_high'] or c['thermal_low']):ramp=p.K1*power_slope(scaled,p.C1)*u[0]/p.ITfPu
    error=u[3]-p.KAct*u[0]
    raw=(p.KpOel+p.KdOel/p.tDOel)*error+u[4]-u[5]
    pid=clip_slope(q['raw'],p.VOel3MinPu,p.VOel3MaxPu)*raw
    free2=p.tC2Oel/p.tB2Oel*(q['pid']-x[6])
    lead2=clip_slope(free2,p.VOel2MinPu,p.VOel2MaxPu)*p.tC2Oel/p.tB2Oel*(pid-u[6])
    free1=p.tC1Oel/p.tB1Oel*(q['lead2']-x[7])
    output=clip_slope(free1,p.VOel1MinPu,p.VOel1MaxPu)*p.tC1Oel/p.tB1Oel*(lead2-u[7])
    result=np.array([(p.KScale*dinput-u[0])/p.tROel,dinverse-p.KFb*u[1],ramp,(u[2]-u[3])/p.tAOel,
        p.KiOel*(error+(pid-raw)/.9),(p.KdOel/p.tDOel*error-u[5])/p.tDOel,
        ((p.tC2Oel-p.tB2Oel)/p.tC2Oel*lead2-u[6])/p.tC2Oel,
        ((p.tC1Oel-p.tB1Oel)/p.tC1Oel*output-u[7])/p.tC1Oel])
    if c['timer_bound']:result[1]=0.
    if c['reference_bound']:result[2]=0.
    return result,output
