"""Analytic IEEEST MODE=1 and authored field-current droop OEL branches.

Voltage gates are held at their strict current branch. No derivative across
that discontinuity or event saltation is asserted. This droop OEL is not IEEE
OEL2C or a manufacturer controller.
"""
import numpy as np

KIND='piecewise_analytic_IEEEST_droop_OEL_network_reduction'


def supported(model):
    if any((model.settings.generator_field_thermal,model.settings.underexcitation_limiters,
            model.settings.oel2c_limiters,model.settings.esst1a_exciters,model.settings.takeover_oels)):
        return False
    if any(m.stabilizer is not None and (m.stabilizer.MODE!=1 or m.stabilizer_connection!='SEXS' or m.exciter is None) for m in model.gen_passports):
        return False
    return bool(model.controllers.pss_addresses or model.excitation_limiters.entries) and all(
        p.model=='field_current_droop_override' for p in model.settings.overexcitation_limiters)


def callback(model):
    if not supported(model):return None
    def evaluate(time,state):
        from power_grid_env.genrou_jacobian import jacobian
        return jacobian(model,state,excitation=True)
    evaluate.jacobian_kind=KIND
    return evaluate


def pss_rows(passport,unit,address,omega):
    """Exact row vectors of the seven-state cascade and unclipped output."""
    p=passport;a,b,c,d,l1,l2,w=unit[address]
    da=(unit[omega]-b-p.A1*a)/p.A2;db=a
    dc=(b-d-p.A3*c)/p.A4;dd=c
    f2=d+p.A5*c+p.A6*dc
    dl1=(f2-l1)/p.T2;v1=l1+p.T1*dl1
    dl2=(v1-l2)/p.T4;v2=l2+p.T3*dl2
    dw=(p.KS*v2-w)/p.T6
    return np.asarray([da,db,dc,dd,dl1,dl2,dw]),p.T5*dw
