"""Local finite-difference reduction of the shared algebraically eliminated RHS."""
import numpy as np
from power_grid_env.coupled_pss import STATE_NAMES


def state_coordinates(model):
    names={}
    for i,m in enumerate(model.settings.motors):
        if model.connected[i]:
            names.update({3*i+j:m.id+'.'+name for j,name in enumerate(['e_real_pu','e_imag_pu','speed_pu'])})
    for i,address in model.cages.addresses.items():
        if model.connected[i]:names.update({address.start:model.settings.motors[i].id+'.cage1_e_real_pu',address.start+1:model.settings.motors[i].id+'.cage1_e_imag_pu'})
    for i,g in enumerate(model.generators):
        names[model.gen_offset+2*i]=g.id+'.delta';names[model.gen_offset+2*i+1]=g.id+'.omega'
    for i,address in model.round_rotors.addresses.items():
        names.update({address.start+j:model.generators[i].id+'.'+name for j,name in enumerate(['e1q','e1d','e2d','e2q'])})
    for mapping,group,labels in [(model.controllers.gov,'TGOV1',['valve_state_pu','turbine_state_pu']),
                                 (model.controllers.avr,'SEXS',['filter_pu','field_state_pu'])]:
        for i,addresses in mapping.items():
            names.update({address:model.generators[i].id+'.'+group+'.'+label for address,label in zip(addresses,labels)})
    for i,address in model.controllers.pss_addresses.items():
        names.update({address.start+j:model.generators[i].id+'.IEEEST.'+label for j,label in enumerate(STATE_NAMES)})
    indices=np.array(sorted(names),dtype=int)
    return indices,[names[int(i)] for i in indices]


def reduced_jacobian(model,state,relative_step=1e-6):
    indices,names=state_coordinates(model);matrix=np.empty((len(indices),len(indices)))
    def branches(values):
        if not model.controllers.pss_addresses:return ()
        v=model.electrical(values)[0][model.gen_buses]
        rotor=values[model.gen_offset:model.rotor_end].reshape(-1,2)
        signals=model.controllers.pss.evaluate(values,rotor[:,1],v)[2]
        result=[]
        for i in model.controllers.pss_addresses:
            m=model.gen_passports[i];p=m.stabilizer;prefix=m.generator_id+'.IEEEST.';raw=signals[prefix+'raw_pu']
            result.append((signals[prefix+'voltage_gate'],raw<=p.LSMIN,raw>=p.LSMAX))
        return tuple(result)
    original_branch=branches(state)
    for column,address in enumerate(indices):
        h=model.controllers.difference_step(state,address,relative_step*max(1.,abs(state[address])))
        if not np.isfinite(h) or h<=0:raise ValueError('nonpositive interior linearization step')
        for attempt in range(32):
            plus,minus=state.copy(),state.copy();plus[address]+=h;minus[address]-=h
            if plus[address]==state[address] or minus[address]==state[address]:raise ValueError('linearization perturbation not representable')
            if branches(plus)==branches(minus)==original_branch:break
            h/=2
        else:raise ValueError('cannot preserve PSS limiter/gate branch during linearization')
        matrix[:,column]=(model.rhs(0.,plus)[indices]-model.rhs(0.,minus)[indices])/(2*h)
    return matrix,names
