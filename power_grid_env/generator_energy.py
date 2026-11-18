"""Opt-in constant-shaft-power GENCLS with lumped losses and exact H*S*w² energy.

Copper/core loss estimates load the shaft and heat the existing thermal node.
The electrical port remains a lossless transient-reactance GENCLS port: this is
not an armature-resistance stamp. Damping is signed external power exchange.
"""
import numpy as np

PROFILE='GENCLS_TGOV1_SEXS_ENERGY'
KEYS=['shaft_power_w','terminal_power_w','heat_input_w','ambient_heat_w','signed_damping_exchange_w','kinetic_rate_w','thermal_rate_w','balance_residual_w','kinetic_energy_j','thermal_energy_j']


def entries(model):return model.generator_heat.entries.items() if model.settings.winding_generator_profile in {PROFILE,'GENCLS_TGOV1_SEXS_STATOR_RT'} else []


def losses(model,state,current,terminal):
    result=np.zeros(len(model.generators))
    for i,(a,p) in entries(model):
        terms=model.generator_heat.terms(state,current,terminal,i,a,p)
        result[i]=terms[1] if model.settings.winding_generator_profile=='GENCLS_TGOV1_SEXS_STATOR_RT' else sum(terms[:2])
    return result


def initialize_power(model,state,current,terminal):
    loss=losses(model,state,current,terminal)
    for i,_ in entries(model):model.gen_mechanical[i]+=loss[i]/model.gen_passports[i].rated_va


def rotor_rates(model,state,current,terminal,mechanical,electrical,derivative):
    loss=losses(model,state,current,terminal)
    for i,_ in entries(model):
        p=model.gen_passports[i];speed=state[model.gen_offset+2*i+1]
        if not np.isfinite(speed) or speed<=0:raise ValueError('GENCLS shaft-power energy profile requires positive rotor speed')
        derivative[i,1]=(mechanical[i]-electrical[i]-loss[i]/p.rated_va-p.damping_pu*(speed-1))/(2*p.inertia_s*speed)


def actions(model,state,u,voltage,out,energy_voltage_directions=None):
    ig=model.generator_currents(state,voltage);terminal=model.generator_terminal_voltage(state,voltage)
    loss=losses(model,state,ig,terminal);rotor=state[model.gen_offset:model.rotor_end].reshape(-1,2)
    mechanical=model.controllers.mechanical(state,rotor[:,1],model.gen_mechanical);electrical=(model.generator_internal(state)*np.conj(ig)).real
    for i,(a,p) in entries(model):
        m=model.gen_passports[i];omega=model.gen_offset+2*i+1;speed=state[omega]
        if speed<=0:raise ValueError('GENCLS shaft-power energy profile requires positive rotor speed')
        dl=p.capacity_j_per_k*out[a]+p.ambient_w_per_k*u[a]
        if model.settings.winding_generator_profile=='GENCLS_TGOV1_SEXS_STATOR_RT':
            delta=model.gen_offset+2*i;eg=model.generator_internal(state)[i];de=1j*eg*u[delta]
            if i in model.controllers.avr:
                from power_grid_env.genrou_jacobian import clip_slope
                _,f=model.controllers.avr[i];e=m.exciter;de+=clip_slope(state[f],e.EMIN,e.EMAX)*u[f]*np.exp(1j*state[delta])
            vt=voltage[model.gen_buses[i]] if model.gen_connected[i] else eg;dt=None
            # Obtain the common algebraic tangent from the caller; no projection
            # of inconsistent DAE voltages is allowed here.
            dt=energy_voltage_directions[model.gen_buses[i]] if model.gen_connected[i] else de
            dl=2*p.core_loss_w*(np.conj(vt)*dt).real
        rate=(mechanical[i]-electrical[i]-loss[i]/m.rated_va-m.damping_pu*(speed-1))/(2*m.inertia_s*speed)
        out[omega]=out[omega]/speed-dl/(2*m.inertia_s*m.rated_va*speed)-rate/speed*u[omega]


def channel_names(model):return [model.generators[i].id+'.energy.'+k for i,_ in entries(model) for k in KEYS]


def signals(model,state,current,voltage,mechanical):
    result={};terminal=model.generator_terminal_voltage(state,voltage)
    for i,(a,p) in entries(model):
        m=model.gen_passports[i];speed=state[model.gen_offset+2*i+1]
        copper,core,removed=model.generator_heat.terms(state,current,terminal,i,a,p)
        shaft=mechanical[i]*m.rated_va;port=(voltage[model.gen_buses[i]]*np.conj(current[i])).real*m.rated_va
        damping=m.damping_pu*(speed-1)*m.rated_va;kinetic_rate=shaft-port-copper-core-damping;thermal_rate=copper+core-removed
        values=[shaft,port,copper+core,removed,damping,kinetic_rate,thermal_rate,shaft-port-damping-removed-kinetic_rate-thermal_rate,m.inertia_s*m.rated_va*speed**2,p.capacity_j_per_k*(state[a]-p.ambient_c)]
        result.update({model.generators[i].id+'.energy.'+k:float(v) for k,v in zip(KEYS,values)})
    return result
