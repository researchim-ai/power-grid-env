"""Opt-in GENCLS stator R(T) Norton stamp and directional products."""
import numpy as np
PROFILE='GENCLS_TGOV1_SEXS_STATOR_RT'


def enabled(model):return model.settings.winding_generator_profile==PROFILE


def entries(model):return model.generator_heat.entries.items() if enabled(model) and hasattr(model,'generator_heat') else []


def seed(model):
    if not enabled(model):return
    lookup={g.id:i for i,g in enumerate(model.generators)}
    for p in model.settings.generator_heat:
        i=lookup[p.generator_id];zb=model.generators[i].ll_v**2/model.gen_passports[i].rated_va
        model.gen_z[i]+=p.resistance_ohm*(1+p.alpha_per_c*(p.initial_c-p.reference_c))/zb


def impedance(model,state):
    z=model.gen_z.copy()
    for i,(a,p) in entries(model):
        t=state[a];factor=1+p.alpha_per_c*(t-p.reference_c)
        if not np.isfinite(t) or factor<=0 or t< -273.15 or t>p.maximum_c:raise ValueError('generator stator temperature outside passport validity')
        z[i]=complex(p.resistance_ohm*factor*model.gen_passports[i].rated_va/model.generators[i].ll_v**2,z[i].imag)
    return z


def admittance(model,state):return np.where(model.gen_connected,model.gen_factors/impedance(model,state),0j)


def temperature_key(model,state):return tuple(float(state[a]) for i,(a,p) in entries(model))


def impedance_product(model,u):
    result=np.zeros((len(model.generators),u.shape[1]),complex)
    for i,(a,p) in entries(model):result[i]=p.resistance_ohm*p.alpha_per_c*model.gen_passports[i].rated_va/model.generators[i].ll_v**2*u[a]
    return result


def channels(model):return [model.generators[i].id+'.stator.'+k for i,_ in entries(model) for k in ['resistance_ohm','resistance_pu','internal_power_w','copper_electrical_loss_w','power_balance_w']]


def signals(model,state,voltage,current):
    out={};z=impedance(model,state);eg=model.generator_internal(state)
    for i,(a,p) in entries(model):
        m=model.gen_passports[i];internal=(eg[i]*np.conj(current[i])).real*m.rated_va;port=(voltage[model.gen_buses[i]]*np.conj(current[i])).real*m.rated_va;cu=z[i].real*abs(current[i])**2*m.rated_va
        data=dict(resistance_ohm=z[i].real*model.generators[i].ll_v**2/m.rated_va,resistance_pu=z[i].real,internal_power_w=internal,copper_electrical_loss_w=cu,power_balance_w=internal-port-cu)
        out.update({model.generators[i].id+'.stator.'+k:float(v) for k,v in data.items()})
    return out
