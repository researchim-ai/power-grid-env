"""Simultaneous PV-generator/network and optional loaded-motor equilibrium search."""
from __future__ import annotations
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse.linalg import splu

MAX_EVALUATIONS=4000


def initialize_coupled(model,*,require_stable=True):
    settings=model.settings;requested=settings.operating_point
    steady=requested is not None
    active=np.flatnonzero(model.connected) if steady else np.array([],dtype=int)
    n=len(active);ng=len(model.generators)
    tolerance=requested.tolerance if requested else 1e-8
    speeds=np.zeros(len(settings.motors))
    state=model.initial_state()
    ptarget=np.array([g.p_w/m.rated_va for g,m in zip(model.generators,model.gen_passports)])
    vtarget=np.array([g.v_set_pu for g in model.generators])
    evaluations=0;best=float('inf');rejected=[]
    def unpack(values):
        speeds[active]=values[:n]
        return values[n:n+ng]+1j*values[n+ng:n+2*ng]
    def residual(values):
        nonlocal evaluations,best
        evaluations+=1
        if evaluations>MAX_EVALUATIONS:raise RuntimeError('coupled initialization evaluation budget exceeded')
        internal=unpack(values)
        voltage,current,em,ig=model.static_electrical(speeds,internal,steady=steady)
        motor=np.array([((em[i]*np.conj(current[i])).real-settings.motors[i].load_torque(speeds[i]))/(2*settings.motors[i].inertia_s) for i in active])
        power=voltage[model.gen_buses]*np.conj(ig)
        error=np.r_[motor,power.real-ptarget,abs(voltage[model.gen_buses])**2-vtarget**2]
        best=min(best,float(max(abs(error))))
        return error
    rhs=np.zeros(len(model.bus_names),dtype=complex);rhs[model.source_bus]=model.source_y*model.source_e
    passive=splu(model.base_y).solve(rhs)
    initial_v=vtarget*np.exp(1j*np.angle(passive[model.gen_buses]))
    initial_e=initial_v+model.gen_z*np.conj(ptarget/initial_v)
    minimum=requested.minimum_speed_pu if requested else 0.
    lower=np.r_[np.full(n,minimum),np.full(2*ng,-10.)]
    upper=np.r_[np.ones(n),np.full(2*ng,10.)]
    try:
        for guess in (.99,.97,.9,(minimum+1)/2):
            values=np.r_[np.full(n,np.clip(guess,minimum+1e-10*(1-minimum),1-1e-10*(1-minimum))),initial_e.real,initial_e.imag]
            values=np.minimum(np.maximum(values,lower+1e-12),upper-1e-12)
            result=least_squares(residual,values,bounds=(lower,upper),xtol=1e-12,ftol=1e-12,gtol=1e-12,max_nfev=250)
            values=result.x
            if max(abs(residual(values)))>tolerance:
                boundary=values.copy();near=1e-5*(1-minimum)
                boundary[:n][1-boundary[:n]<near]=1.
                boundary[:n][boundary[:n]-minimum<near]=minimum
                if np.array_equal(boundary,values) or max(abs(residual(boundary)))>tolerance:continue
                values=boundary
            internal=unpack(values)
            voltage,current,em,ig=model.static_electrical(speeds,internal,steady=steady)
            if np.any(abs(internal)<1e-8):
                rejected.append('zero_generator_internal_voltage');continue
            power=voltage[model.gen_buses]*np.conj(ig)*np.array([m.rated_va for m in model.gen_passports])
            q_bad=[g.id for g,q in zip(model.generators,power.imag) if
                (g.q_min_var is not None and q<g.q_min_var-1e-3) or (g.q_max_var is not None and q>g.q_max_var+1e-3)]
            if q_bad:return None,{'reason':'generator_initial_q_limit','generators':q_bad}
            model.gen_voltage=abs(internal)
            model.gen_mechanical=(internal*np.conj(ig)).real
            state[:model.electrical_size]=np.column_stack([em.real,em.imag,speeds]).ravel()
            if steady:model.cages.initialize(state,current,speeds)
            state[model.gen_offset:model.rotor_end]=np.column_stack([np.angle(internal),np.ones(ng)]).ravel()
            rotor=state[model.gen_offset:model.rotor_end].reshape(-1,2)
            generator_error=model.round_rotors.initialize(state,rotor,internal,ig,model.gen_voltage)
            if generator_error:return None,generator_error
            if steady and requested.thermal_state=='equilibrium':
                for i,address in model.thermal_indices.items():state[address]=abs(current[i])**2
            controller_error=model.controllers.initialize(state,voltage[model.gen_buses],model.gen_voltage,model.gen_mechanical,tolerance)
            if controller_error:return None,controller_error
            limits=[settings.motors[i].id for i,address in model.thermal_indices.items() if state[address]>=settings.motors[i].thermal.trip_level]
            if limits:return None,{'reason':'initial_thermal_limit','motors':limits}
            derivative=model.rhs(0.,state)
            checked=np.r_[np.r_[np.arange(model.electrical_size),*[np.arange(a.start,a.stop) for a in model.cages.addresses.values()]],np.arange(model.gen_offset,len(state))] if steady else np.arange(model.gen_offset,len(state))
            dynamic=float(max(abs(derivative[checked])))
            if not np.isfinite(derivative).all() or dynamic>tolerance:
                rejected.append('original_dynamic_residual');continue
            alpha=None
            if steady:
                flags=model.controllers.pss.boundaries(state,voltage[model.gen_buses])
                if flags:return None,{'reason':'controller_boundary_operating_point','boundaries':flags}
                from power_grid_env.coupled_linearization import reduced_jacobian
                jac,_=reduced_jacobian(model,state)
                modes=np.linalg.eigvals(jac);alpha=float(max(modes.real))
                if not np.isfinite(modes).all() or (require_stable and alpha>=-1e-7):
                    rejected.append('unstable_or_near_axis_coupled_equilibrium');continue
            return state.copy(),{'mode':'coupled_loaded_equilibrium' if steady else 'PV_generator_with_cold_motors',
                'evaluations':evaluations,'electrical_residual':float(max(abs(residual(values)))),
                'checked_dynamic_residual_per_s':dynamic,'connected_machine_spectral_abscissa_per_s':alpha,
                'thermal_state':requested.thermal_state if steady else 'passport',
                'thermal_residual_per_s':{settings.motors[i].id:float(derivative[address]) for i,address in model.thermal_indices.items()},
                'generator_initial':{g.id:{'internal_voltage_pu':float(abs(internal[i])),'field_pu':float(model.gen_voltage[i]),'model':model.gen_passports[i].model,'delta_rad':float(rotor[i,0]),
                    'p_w':float(power[i].real),'q_var':float(power[i].imag),'mechanical_power_pu':float(model.gen_mechanical[i])} for i,g in enumerate(model.generators)},
                'branch_note':'bounded local initialization; no uniqueness/global or hybrid protection stability proof'}
    except (ValueError,RuntimeError,ArithmeticError,np.linalg.LinAlgError) as exc:
        return None,{'reason':'coupled_initialization_search_failed','error':str(exc),'evaluations':evaluations}
    return None,{'reason':'no_qualified_coupled_operating_point','evaluations':evaluations,
                 'best_residual':best if np.isfinite(best) else None,'rejected_candidates':rejected}
