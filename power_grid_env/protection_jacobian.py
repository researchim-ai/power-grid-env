"""MNLEX2 and rotor I-squared continuous derivatives in the current hybrid mode.

No reset or saltation derivative is supplied. OEL2C retains finite differences.
"""
import numpy as np

KIND='piecewise_analytic_MNLEX2_field_I2_network_reduction'


def supported(model,*,allow_oel=False):
    if not (model.field_thermal.entries or model.underexcitation_limiters.entries or (allow_oel and model.oel2c.entries)):return False
    if model.settings.oel2c_limiters and not allow_oel:return False
    targets=set(model.esst1a.entries)
    if any(model.gen_passports[i].model!='GENROU' or i in model.controllers.avr for i in targets):return False
    if any(i not in targets for i in model.takeover_oel.entries):return False
    if any(p.model!='field_current_droop_override' for p in model.settings.overexcitation_limiters):return False
    if any(p.model!='FieldCurrentPI_AbsoluteTakeover_v1' for p in model.settings.takeover_oels):return False
    return all(m.stabilizer is None or (m.stabilizer.MODE==1 and m.stabilizer_connection==('external_ESST1A' if i in targets else 'SEXS')) for i,m in enumerate(model.gen_passports))


def callback(model):
    if not supported(model):return None
    def evaluate(time,state):
        from power_grid_env.genrou_jacobian import jacobian
        return jacobian(model,state,protections=True)
    evaluate.jacobian_kind=KIND
    from power_grid_env.jacobian_columns import attach
    def evaluate_columns(time,state,cols,ctx):
        from power_grid_env.genrou_jacobian import jacobian
        return jacobian(model,state,protections=True,columns=cols,electrical=ctx)
    def actions(time,state,u,ctx,voltage_directions=None):
        from power_grid_env.genrou_jacobian import jacobian
        return jacobian(model,state,protections=True,directions=u,electrical=ctx,voltage_directions=voltage_directions)
    return attach(evaluate,model,evaluate_columns,actions)


def mnlex2_rows(p,unit,a,state,power,dpower,voltage,dvoltage):
    """Circle uses machine-base terminal P,Q and squared terminal magnitude."""
    from power_grid_env.genrou_jacobian import clip_slope
    v2=abs(voltage)**2;dv2=2*(np.conj(voltage)*dvoltage).real
    shifted=power.imag-p.Q_0*v2
    de=2*power.real*dpower.real+2*shifted*(dpower.imag-p.Q_0*dv2)-2*p.Radius**2*v2*dv2
    dout=clip_slope(state[a],0.,p.MEL_MAX)*unit[a]
    memory=unit[a+1];feedback=p.K_F2/p.T_F2*(dout-memory)
    return np.array([(p.K_M*(de-feedback)-unit[a])/p.T_M,(dout-memory)/p.T_F2])
