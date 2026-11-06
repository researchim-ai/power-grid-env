"""Piecewise analytic GENCLS/GENROU, TGOV1/SEXS and cage-motor reduction.

Derivatives are for the current discrete mode, not event saltation matrices.
At a clipping corner the outward generalized slope is zero. Held controller
rows are zero exactly as in ControllerBoundaries.derivative.
"""
import math
from types import SimpleNamespace
import numpy as np
from power_grid_env.motor_jacobian import jacobian as motor_jacobian
from power_grid_env.coupled_generators import saturation

KIND = 'piecewise_analytic_GENROU_TGOV1_SEXS_network_reduction'


def supported(model):
    return not (any(m.stabilizer is not None for m in model.gen_passports) or
                any((model.settings.overexcitation_limiters, model.settings.generator_field_thermal,
                     model.settings.underexcitation_limiters, model.settings.oel2c_limiters,
                     model.settings.esst1a_exciters, model.settings.takeover_oels)))


def callback(model):
    if not supported(model):return None
    def evaluate(time,state):return jacobian(model,state)
    evaluate.jacobian_kind=KIND
    from power_grid_env.jacobian_columns import attach
    def evaluate_columns(time,state,cols,ctx):
        from power_grid_env.genrou_jacobian import jacobian
        return jacobian(model,state,columns=cols,electrical=ctx)
    def actions(time,state,u,ctx,voltage_directions=None):
        from power_grid_env.genrou_jacobian import jacobian
        return jacobian(model,state,directions=u,electrical=ctx,voltage_directions=voltage_directions)
    return attach(evaluate,model,evaluate_columns,actions)


def clip_slope(value,low,high):
    return float(low < value < high)


def saturation_slope(machine,flux):
    if machine.saturation=='disabled':return 0.
    ratio=math.sqrt(machine.saturation_s10/(1.2*machine.saturation_s12))
    threshold=(1-1.2*ratio)/(1-ratio)
    if flux<=0:
        if threshold<0:raise ValueError('quadratic saturation is not differentiable at zero flux with negative threshold')
        return 0.
    if flux<=threshold:return 0.
    gain=machine.saturation_s10/(1-threshold)**2
    return gain*(1-(threshold/flux)**2)


def jacobian(model,state,*,excitation=False,esst=False,protections=False,oel=False,columns=None,electrical=None,directions=None,voltage_directions=None):
    if oel:protections=True
    if protections:
        from power_grid_env.protection_jacobian import supported as protection_supported
        if not protection_supported(model,allow_oel=oel):raise ValueError('unsupported protection Jacobian profile')
        esst=bool(model.esst1a.entries);excitation=not esst
    if excitation and esst:raise ValueError('choose one analytic extension')
    if esst:
        from power_grid_env.esst1a_jacobian import supported as esst_supported,assemble
        if not protections and not esst_supported(model):raise ValueError('analytic ESST1A Jacobian excludes this controller profile')
        from power_grid_env.pss_droop_jacobian import pss_rows
    elif excitation:
        from power_grid_env.pss_droop_jacobian import supported as extended_supported,pss_rows
        if not protections and not extended_supported(model):raise ValueError('analytic excitation Jacobian excludes this controller profile')
    elif not supported(model):raise ValueError('analytic GENROU Jacobian excludes PSS and excitation limiters')
    state=np.asarray(state);dimension=model.takeover_oel.end if esst or protections else model.excitation_limiters.end if excitation else model.generator_thermal.end
    if state.shape!=(dimension,) or not np.isrealobj(state) or not np.isfinite(state).all():raise ValueError('invalid GENROU Jacobian state')
    nm=len(model.settings.motors);ng=len(model.generators)
    real=np.arange(nm)*3;imag=real+1;speed=real+2
    delta=model.gen_offset+np.arange(ng)*2;omega=delta+1
    if directions is not None:
        if columns is not None:raise ValueError('choose columns or tangent directions')
        from power_grid_env.jacobian_columns import tangent_seeds
        unit=tangent_seeds(dimension,directions);width=unit.shape[1];positions={}
    elif columns is None:
        unit=np.eye(dimension);width=dimension;positions={i:i for i in range(dimension)}
    else:
        from power_grid_env.jacobian_columns import seeds
        columns,unit=seeds(dimension,columns);width=len(columns);positions={int(c):j for j,c in enumerate(columns)}
    voltage,im,_,_=model.electrical(state) if electrical is None else electrical
    eg=model.generator_internal(state);ig=model.generator_currents(state,voltage)
    deg=np.zeros((ng,width),dtype=complex);field_gradient=np.zeros((ng,width))
    flux_gradient={}
    for i,m in enumerate(model.gen_passports):
        if directions is not None:deg[i]=1j*eg[i]*unit[delta[i]]
        elif delta[i] in positions:deg[i,positions[delta[i]]]=1j*eg[i]
        if i in model.controllers.avr:
            _,field=model.controllers.avr[i];p=m.exciter
            if directions is not None:field_gradient[i]=clip_slope(state[field],p.EMIN,p.EMAX)*unit[field]
            elif field in positions:field_gradient[i,positions[field]]=clip_slope(state[field],p.EMIN,p.EMAX)
        if i in model.round_rotors.addresses:
            a=model.round_rotors.addresses[i];gd1,gd2,gq1,_,_=model.round_rotors.coefficients[i]
            dpd=gd1*unit[a.start]+gd2*(m.x_d_prime_pu-m.leakage_pu)*unit[a.start+2]
            dpq=gq1*unit[a.start+1]+(1-gq1)*unit[a.start+3]
            deg[i]+=(dpd-1j*dpq)*np.exp(1j*state[delta[i]])
            flux_gradient[i]=(dpd,dpq)
        else:
            deg[i]+=field_gradient[i]*np.exp(1j*state[delta[i]])
    dem=unit[real].astype(complex)+1j*unit[imag]
    direct=directions is not None or (columns is not None and model.settings.jacobian_network=='direct_solve')
    if voltage_directions is not None:
        dv=np.asarray(voltage_directions);dvm,dvg=dv[model.motor_buses],dv[model.gen_buses]
    elif direct:
        from power_grid_env.network_sensitivity import directional_response
        dv=directional_response(model,np.r_[model.motor_buses,model.gen_buses],np.r_[model.motor_y,model.active_gen_y],np.r_[model.motor_buses,model.gen_buses],np.vstack([dem,deg]))
        dvm,dvg=dv[:nm],dv[nm:]
    else:
        cached=getattr(model,'_round_voltage_response',None)
        if cached is None or cached[0] is not model.lu:
            from power_grid_env.network_sensitivity import port_response
            response=port_response(model,np.r_[model.motor_buses,model.gen_buses],np.r_[model.motor_y,model.active_gen_y],np.r_[model.motor_buses,model.gen_buses])
            cached=(model.lu,response);model._round_voltage_response=cached
        response=cached[1]
        dvm=response[:nm,:nm]@dem+response[:nm,nm:]@deg
        dvg=response[nm:,:nm]@dem+response[nm:,nm:]@deg
    dim=np.where(model.connected[:,None],(dvm-dem)/model.zmotor[:,None],0j)
    dig=np.where(model.gen_connected[:,None],(deg-dvg)/model.gen_z[:,None],0j)
    # The existing motor block includes all internal motor coordinates. Add
    # only the external (generator/controller) columns, avoiding double count.
    if not direct:model._motor_current_response=np.where(model.connected[:,None],(response[:nm,:nm]-np.eye(nm))/model.zmotor[:,None],0j)
    view=SimpleNamespace(**vars(model));view.electrical=lambda ignored:(voltage,im,None,None)
    result=np.zeros((dimension,width))
    end=model.motor_state_end
    if columns is not None or directions is not None:
        from power_grid_env.motor_jacobian import tangent_rows
        result[:end]=tangent_rows(model,state,im,dim,unit)
    else:
        result[:end,:end]=motor_jacobian(view,state[:end])
        external=dim[:,end:]
        for i,m in enumerate(model.settings.motors):
            coefficient=1j*model.constants[i][1]/model.constants[i][2]
            if i in model.cages.addresses:coefficient=model.cages.coefficients[i][2][0]
            d=coefficient*external[i]
            result[real[i],end:]=d.real;result[imag[i],end:]=d.imag
            emf=state[real[i]]+1j*state[imag[i]]
            result[speed[i],end:]=(emf*np.conj(external[i])).real/(2*m.inertia_s)
            if i in model.thermal_indices:result[model.thermal_indices[i],end:]=2*(np.conj(im[i])*external[i]).real/m.thermal.time_constant_s
            if i in model.cages.addresses:
                a=model.cages.addresses[i];d=model.cages.coefficients[i][2][1]*external[i]
                result[a.start,end:]=d.real;result[a.start+1,end:]=d.imag
    reaction_gradient={}
    if excitation or esst:
        terminal=model.generator_terminal_voltage(state,voltage)
        _,pss_output,pss_signals=model.controllers.pss.evaluate(state,state[omega],terminal)
        output_gradient=np.zeros((ng,width));bias_gradient=np.zeros((ng,width))
        bias=model.excitation_bias(state) if protections else model.excitation_limiters.bias(state)
        for i,address in model.controllers.pss_addresses.items():
            passport=model.gen_passports[i].stabilizer
            rows,raw_gradient=pss_rows(passport,unit,address,omega[i]);result[address]=rows
            prefix=model.gen_passports[i].generator_id+'.IEEEST.'
            raw=pss_signals[prefix+'raw_pu']
            if pss_signals[prefix+'voltage_gate']:
                output_gradient[i]=clip_slope(raw,passport.LSMIN,passport.LSMAX)*raw_gradient
        for i,(a,passport) in model.excitation_limiters.entries.items():
            if directions is not None:bias_gradient[i]=clip_slope(state[a+1],0.,passport.maximum_voltage_bias_pu)*unit[a+1]
            elif a+1 in positions:bias_gradient[i,positions[a+1]]=clip_slope(state[a+1],0.,passport.maximum_voltage_bias_pu)
    if protections:
        uel_gradient=np.zeros((ng,width))
        for i,(a,p) in model.underexcitation_limiters.entries.items():
            if directions is not None:uel_gradient[i]=clip_slope(state[a],0.,p.MEL_MAX)*unit[a]
            elif a in positions:uel_gradient[i,positions[a]]=clip_slope(state[a],0.,p.MEL_MAX)
            bias_gradient[i]-=uel_gradient[i]
            terminal_i=terminal[i];dt=dvg[i] if model.gen_connected[i] else deg[i]
            from power_grid_env.protection_jacobian import mnlex2_rows
            dpower=dt*np.conj(ig[i])+terminal_i*np.conj(dig[i])
            result[a:a+2]=mnlex2_rows(p,unit,a,state,terminal_i*np.conj(ig[i]),dpower,terminal_i,dt)
    if oel:
        from power_grid_env.oel2c_jacobian import rows as oel_rows
        for i,(a,p,k) in model.oel2c.entries.items():
            result[a:a+8],out=oel_rows(k,state[a:a+8],unit[a:a+8],np.zeros(width))
            bias_gradient[i]-=out
    if esst:esst_values=model.esst1a.values(state,voltage,ig)
    for i,m in enumerate(model.gen_passports):
        mechanical=np.zeros(width)
        if i in model.controllers.gov:
            valve,lag=model.controllers.gov[i];p=m.governor
            bounded=np.clip(state[valve],p.VMIN,p.VMAX);slope=clip_slope(state[valve],p.VMIN,p.VMAX)
            mechanical=(p.T2/p.T3)*slope*unit[valve]+(1-p.T2/p.T3)*unit[lag]-p.Dt*unit[omega[i]]
            demand=model.controllers.pref[i]-(state[omega[i]]-1)/p.R
            rate=(demand-state[valve])/p.T1
            blocked=(state[valve]>=p.VMAX and rate>0) or (state[valve]<=p.VMIN and rate<0)
            if not blocked or model.settings.event_policy=='coincident_roots_v1':result[valve]=(-unit[omega[i]]/p.R-unit[valve])/p.T1
            result[lag]=(slope*unit[valve]-unit[lag])/p.T3
        power=(deg[i]*np.conj(ig[i])+eg[i]*np.conj(dig[i])).real
        if directions is not None:result[delta[i]]=2*math.pi*model.network.frequency_hz*unit[omega[i]]
        elif omega[i] in positions:result[delta[i],positions[omega[i]]]=2*math.pi*model.network.frequency_hz
        result[omega[i]]=(mechanical-power-m.damping_pu*unit[omega[i]])/(2*m.inertia_s)
        if i in model.round_rotors.addresses:
            a=model.round_rotors.addresses[i];gd1,gd2,gq1,gq2,gqd=model.round_rotors.coefficients[i]
            dpd,dpq=flux_gradient[i];pd,pq=model.round_rotors.fluxes(state,i)
            rot=np.exp(-1j*state[delta[i]]);ir=ig[i]*rot
            dir_=dig[i]*rot-1j*ir*unit[delta[i]];did=-dir_.imag;diq=dir_.real
            flux=math.hypot(pd,pq);se=saturation(m,flux)
            dse=saturation_slope(m,flux)*(pd*dpd+pq*dpq)/flux if flux>0 else np.zeros(width)
            # Check the zero-flux singular case even though no division is needed.
            if flux==0:saturation_slope(m,flux)
            deq,ded,dpsid,dpsiq=unit[a.start:a.stop]
            dad=deq+(m.x_d_pu-m.x_d_prime_pu)*(gd1*did-gd2*dpsid+gd2*deq)+se*dpd+pd*dse
            daq=ded+(m.x_q_pu-m.x_q_prime_pu)*(gq2*ded-gq2*dpsiq-gq1*diq)+gqd*(se*dpq+pq*dse)
            if (excitation or protections) and (i in model.excitation_limiters.entries or i in model.field_thermal.entries):reaction_gradient[i]=dad
            if oel and i in model.oel2c.entries:
                b,p,k=model.oel2c.entries[i]
                rotor=state[model.gen_offset:model.rotor_end].reshape(-1,2)
                reaction=model.round_rotors.field_reaction(state,rotor,ig)[i]
                result[b]+=p.KScale*np.sign(reaction)*dad/(p.rated_field_reaction_pu*p.tROel)
            if esst and i in model.esst1a.entries:
                terminal=voltage[model.gen_buses[i]] if model.gen_connected[i] else eg[i]
                dt=dvg[i] if model.gen_connected[i] else deg[i]
                magnitude=abs(terminal)
                if magnitude==0:raise ValueError('ESST1A terminal magnitude is not differentiable at zero voltage')
                dmag=(np.conj(terminal)*dt).real/magnitude
                rotor=state[model.gen_offset:model.rotor_end].reshape(-1,2)
                reaction=model.round_rotors.field_reaction(state,rotor,ig)[i]
                field_gradient[i],esst_rows,pi_rows=assemble(model,i,state,magnitude,reaction,dmag,dad,output_gradient[i],unit,esst_values[i],duel=uel_gradient[i] if protections else None)
                exciter_address,_=model.esst1a.entries[i];result[exciter_address:exciter_address+5]=esst_rows
                if pi_rows is not None:
                    pi_address,_=model.takeover_oel.entries[i];result[pi_address:pi_address+2]=pi_rows
            if protections:field_gradient[i]*=model.field_thermal.field_enabled[i]
            result[a.start]=(field_gradient[i]-dad)/m.td_prime_s
            result[a.start+1]=-daq/m.tq_prime_s
            result[a.start+2]=(deq-dpsid-(m.x_d_prime_pu-m.leakage_pu)*did)/m.td_subtransient_s
            result[a.start+3]=(ded-dpsiq+(m.x_q_prime_pu-m.leakage_pu)*diq)/m.tq_subtransient_s
        if i in model.controllers.avr:
            f,field=model.controllers.avr[i];p=m.exciter
            terminal=voltage[model.gen_buses[i]] if model.gen_connected[i] else eg[i]
            dt=dvg[i] if model.gen_connected[i] else deg[i]
            magnitude=abs(terminal)
            if magnitude==0:raise ValueError('SEXS terminal magnitude is not differentiable at zero voltage')
            derror=-(np.conj(terminal)*dt).real/magnitude
            error=model.controllers.vref[i]-magnitude
            if excitation or esst:
                derror=derror+output_gradient[i]-bias_gradient[i]
                error=error+pss_output[i]-bias[i]
            result[f]=(derror-unit[f])/p.TB
            rate=(p.K*(p.TATB*error+(1-p.TATB)*state[f])-state[field])/p.TE
            blocked=(state[field]>=p.EMAX and rate>0) or (state[field]<=p.EMIN and rate<0)
            if not blocked or model.settings.event_policy=='coincident_roots_v1':result[field]=(p.K*(p.TATB*derror+(1-p.TATB)*unit[f])-unit[field])/p.TE
    for i,(a,p) in model.generator_thermal.entries.items():
        result[a]=2*(np.conj(ig[i])*dig[i]).real/p.time_constant_s-unit[a]/p.time_constant_s
    if protections:
        reaction=model.field_thermal.reaction(state,ig)
        for i,(a,p) in model.field_thermal.entries.items():
            result[a]=(2*reaction[i]*reaction_gradient[i]/p.rated_field_reaction_pu**2-unit[a])/p.time_constant_s
    if (excitation or protections) and model.excitation_limiters.entries:
        reaction=model.excitation_limiters.reaction(state,ig)
        for i,(a,p) in model.excitation_limiters.entries.items():
            result[a]=(np.sign(reaction[i])*reaction_gradient[i]/p.rated_field_reaction_pu-unit[a])/p.measurement_time_s
            excess=state[a]-p.current_limit_pu
            raw=p.feedback_gain_pu*max(excess,0.)
            gain=p.feedback_gain_pu if excess>0. and raw<p.maximum_voltage_bias_pu else 0.
            result[a+1]=(gain*unit[a]-unit[a+1])/p.bias_time_s
    for a in model.controller_boundaries.active:result[a]=0.
    if not np.isfinite(result).all():raise ValueError('nonfinite GENROU Jacobian')
    return result
