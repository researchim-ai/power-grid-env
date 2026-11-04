"""Piecewise ESST1A/absolute-PI OEL derivatives, current topology and gate branch.

Continuous rates only: regulator wind-out remains free and inward resets are
not differentiated. Selector ties use the first operand; rectifier clip ties
use the moving bound. Neither reset/saltation nor hybrid stability is claimed.
"""
import numpy as np

KIND='piecewise_analytic_ESST1A_absolute_PI_OEL_network_reduction'


def supported(model):
    if not model.esst1a.entries or any((model.settings.overexcitation_limiters,model.settings.generator_field_thermal,
        model.settings.underexcitation_limiters,model.settings.oel2c_limiters)):
        return False
    targets=set(model.esst1a.entries)
    if any(model.gen_passports[i].model!='GENROU' or i in model.controllers.avr for i in targets):return False
    if any(i not in targets for i in model.takeover_oel.entries):return False
    for i,m in enumerate(model.gen_passports):
        if m.stabilizer is not None:
            connection='external_ESST1A' if i in targets else 'SEXS'
            if m.stabilizer.MODE!=1 or m.stabilizer_connection!=connection:return False
    return all(p.model=='FieldCurrentPI_AbsoluteTakeover_v1' for p in model.settings.takeover_oels)


def callback(model):
    if not supported(model):return None
    def evaluate(time,state):
        from power_grid_env.genrou_jacobian import jacobian
        return jacobian(model,state,esst=True)
    evaluate.jacobian_kind=KIND
    return evaluate


def assemble(model,i,state,voltage,current,dvoltage,dcurrent,dpss,unit,q,duel=None):
    """Full row vectors for the field output, five exciter states and PI states."""
    from power_grid_env.genrou_jacobian import clip_slope
    a,p=model.esst1a.entries[i];ports=model.esst1a.ports[i]
    sensor,z1,z2,raw,zf=unit[a:a+5]
    regulator=clip_slope(state[a+3],p.V_AMIN,p.V_AMAX)*raw
    current_limit=p.K_LR*dcurrent if p.K_LR*(current-p.I_LR)>0. else np.zeros(len(state))
    demand=regulator-current_limit
    feedback=p.K_F/p.T_F*(demand-zf)
    error=-feedback-sensor+dpss+(0. if duel is None else duel)
    clipped=clip_slope(q['error_pu'],p.V_IMIN,p.V_IMAX)*error
    selected=clipped if q['error_limited_pu']>=ports['uel_error_floor_pu'] else np.zeros(len(state))
    lead1=p.T_C/p.T_B*selected+(1-p.T_C/p.T_B)*z1
    lead2=p.T_C1/p.T_B1*lead1+(1-p.T_C1/p.T_B1)*z2
    cap=np.zeros(len(state));pi_rows=None
    if i in model.takeover_oel.entries:
        b,pi=model.takeover_oel.entries[i]
        free=state[b+1]+pi.proportional_gain*(pi.current_limit_pu-state[b])
        clipped_ceiling=float(np.clip(free,pi.minimum_field_pu,pi.maximum_field_pu))
        dfree=unit[b+1]-pi.proportional_gain*unit[b]
        dceiling=clip_slope(free,pi.minimum_field_pu,pi.maximum_field_pu)*dfree
        if clipped_ceiling<ports['oel_ceiling_pu']:cap=dceiling
        pi_rows=np.array([(dcurrent-unit[b])/pi.sensor_time_s,
                         -pi.integral_gain*unit[b]+(dceiling-dfree)/pi.tracking_time_s])
    high=demand if q['field_demand_pu']>=ports['uel_field_floor_pu'] else np.zeros(len(state))
    takeover=high if q['field_floor_selected_pu']<=q['effective_oel_ceiling_pu'] else cap
    lower=p.V_RMIN*dvoltage;upper=p.V_RMAX*dvoltage-p.K_C*dcurrent
    if q['rectifier_lower_pu']==q['rectifier_upper_pu'] or q['takeover_selected_pu']>=q['rectifier_upper_pu']:field=upper
    elif q['takeover_selected_pu']<=q['rectifier_lower_pu']:field=lower
    else:field=takeover
    rows=np.array([(dvoltage-sensor)/p.T_R,(selected-z1)/p.T_B,(lead1-z2)/p.T_B1,
                   (p.K_A*lead2-raw)/p.T_A,(demand-zf)/p.T_F])
    return field,rows,pi_rows
