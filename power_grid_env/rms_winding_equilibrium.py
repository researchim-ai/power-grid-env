"""Bounded simultaneous motor/network/two-node heat equilibrium.

Opt-in single/two-cage motor-only profile with a common rotor heat node. Analytic reduced Jacobian of steady
T-port elimination, followed by original ODE/KCL and full physical-spectrum
checks. A local bounded search is not a global nonexistence/uniqueness proof.
"""
import math
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu

from power_grid_env.rms_winding import operator


def initialize_electrothermal(model, *, require_stable=True):
    s=model.settings;p=s.operating_point;bank=model.windings
    motors=s.motors;n=len(motors)
    if bank is None or len(bank.addresses)!=n or not 1<=n<=32 or not np.all(model.connected) or getattr(model,'generators',[]):
        return None,dict(reason=('electrothermal_equilibrium_requires_1_to_32_connected_thermal_cage_motors' if any(m.model=='double_cage' for m in motors) else 'electrothermal_equilibrium_requires_1_to_32_connected_single_cage_thermal_motors'))
    if any(m.thermal for m in motors) and p.thermal_state!='equilibrium':
        return None,dict(reason='electrothermal_equilibrium_requires_relay_replica_equilibrium')
    for m in motors:
        h=m.winding_thermal
        if (h.stator_ambient_w_per_k+h.cross_w_per_k)*(h.rotor_ambient_w_per_k+h.cross_w_per_k)-h.cross_w_per_k**2<=0:
            return None,dict(reason='underdetermined_winding_thermal_network',motor_id=m.id,
                             scope='positive definite passive heat-removal matrix required for this isolated stationary branch')
    if hasattr(model,'initial_state'):template=model.initial_state()
    else:
        template=np.r_[np.zeros(model.electrical_size),[m.thermal.initial_level for m in motors if m.thermal],np.zeros(model.motor_state_end-model.electrical_size-len(model.thermal_indices))]
        bank.initialize(template)
    wb=2*math.pi*model.network.frequency_hz;calls=0;best=float('inf');cache=None
    lower=np.array([p.minimum_speed_pu]*n+[m.winding_thermal.ambient_c for m in motors]*2)
    upper=np.array([1.]*n+[m.winding_thermal.maximum_c-1e-7 for m in motors]*2)
    if np.any(lower>=upper):return None,dict(reason='empty_electrothermal_temperature_bounds')

    def evaluate(v):
        nonlocal calls,best,cache
        if cache is not None and np.array_equal(v,cache[0]):return cache[1:]
        calls+=1
        if calls>2000:raise RuntimeError('electrothermal initialization evaluation budget exceeded')
        x=template.copy();speed=v[:n]
        for i,a in bank.addresses.items():x[a]=[v[n+i],v[2*n+i]]
        z,rr=bank.parameters(x);a=np.array([wb*r/(m.rotor_x_pu+m.magnetizing_x_pu) for r,m in zip(rr,motors)])
        diff=np.array([c[1] for c in model.constants]);den=a+1j*wb*(1-speed)
        response=1j*diff*a/den;cage_response={}
        for i in model.cages.addresses:
            cage_response[i]=bank.cage_steady(i,speed[i],x);response[i]=cage_response[i][0][0]
        ze=z+response;ym=model.factors/ze
        matrix=model.matrix.tolil(copy=True)
        for bus,dy in zip(model.motor_buses,ym-model.motor_y):matrix[bus,bus]+=dy
        factor=splu(csc_matrix(matrix));rhs=model.network_injection(x)
        # Template electrical states are zero: RHS is only the external source.
        voltage=factor.solve(rhs);current=voltage[model.motor_buses]/ze;e=response*current
        x[:model.electrical_size]=np.column_stack([e.real,e.imag,speed]).ravel()
        for i,address in model.cages.addresses.items():
            aux=cage_response[i][0][1]*current[i];x[address]=[aux.real,aux.imag]
        for i,address in model.thermal_indices.items():x[address]=abs(current[i])**2
        rates=bank.derivatives(x,current,(e*np.conj(current)).real)
        indices=np.array([3*i+2 for i in range(n)]+[bank.addresses[i].start for i in range(n)]+[bank.addresses[i].start+1 for i in range(n)])
        heat_scales=np.r_[np.ones(n),[m.winding_thermal.stator_capacity_j_per_k/m.rated_va for m in motors],[m.winding_thermal.rotor_capacity_j_per_k/m.rated_va for m in motors]]
        residual=rates[indices]*heat_scales;best=min(best,float(max(abs(residual))))
        width=3*n;u=np.zeros((len(x),width));u[np.arange(n)*3+2,np.arange(n)]=1.
        dr=np.zeros((n,width),complex);dz=np.zeros((n,width),complex)
        for i,m in enumerate(motors):
            h=m.winding_thermal;address=bank.addresses[i]
            u[address.start,n+i]=1.;u[address.start+1,2*n+i]=1.
            da=wb*m.rotor_r_pu*h.alpha_per_c/(m.rotor_x_pu+m.magnetizing_x_pu)
            dr[i,i]=1j*diff[i]*a[i]*1j*wb/den[i]**2
            dr[i,2*n+i]=1j*diff[i]*(1j*wb*(1-speed[i]))/den[i]**2*da
            if i in cage_response:
                dr[i,i]=cage_response[i][1][0];dr[i,2*n+i]=cage_response[i][2][0]
            dz[i]=dr[i];dz[i,n+i]+=m.stator_r_pu*h.alpha_per_c
        dy=-model.factors[:,None]*dz/ze[:,None]**2
        forcing=np.zeros((len(voltage),width),complex)
        np.add.at(forcing,model.motor_buses,-dy*voltage[model.motor_buses,None])
        dv=factor.solve(forcing);di=(dv[model.motor_buses]-current[:,None]*dz)/ze[:,None]
        de=dr*current[:,None]+response[:,None]*di
        for i,address in model.cages.addresses.items():
            q,dw,dt=cage_response[i];dq=np.zeros(width,complex);dq[i]=dw[1];dq[2*n+i]=dt[1]
            daux=dq*current[i]+q[1]*di[i];u[address.start]=daux.real;u[address.start+1]=daux.imag
        u[np.arange(n)*3]=de.real;u[np.arange(n)*3+1]=de.imag
        for i,address in model.thermal_indices.items():u[address]=2*(np.conj(current[i])*di[i]).real
        jac=bank.actions(0.,x,u,(voltage,current,None,None),voltage_directions=dv)[indices]*heat_scales[:,None]
        cache=(v.copy(),residual,jac,x,voltage,current)
        return cache[1:]

    fixed={i:1. for i,m in enumerate(motors) if m.load_torque(1.)==0.}
    for i,m in enumerate(motors):
        if i in fixed and m.winding_thermal.cross_w_per_k==0.:fixed[2*n+i]=m.winding_thermal.ambient_c
    free=np.array([i for i in range(3*n) if i not in fixed],dtype=int)
    def expand(values):
        full=np.empty(3*n);full[free]=values
        for i,value in fixed.items():full[i]=value
        return full

    try:
        rejected=[]
        for guess in [.99,.97,.9,(1+p.minimum_speed_pu)/2]:
            temperatures=[m.winding_thermal.initial_temperature_c or (m.winding_thermal.ambient_c+10,)*2 for m in motors]
            initial=np.r_[np.full(n,guess),[t[0] for t in temperatures],[t[1] for t in temperatures]]
            initial=np.clip(initial,lower+1e-8,upper-1e-8)
            result=least_squares(lambda v:evaluate(expand(v))[0],initial[free],jac=lambda v:evaluate(expand(v))[1][:,free],bounds=(lower[free],upper[free]),
                                 xtol=1e-13,ftol=1e-13,gtol=1e-13,max_nfev=300)
            residual,jac,x,voltage,current=evaluate(expand(result.x))
            if max(abs(residual))>p.tolerance:
                rejected.append(dict(reason='stationary_balance_residual',maximum=float(max(abs(residual))),upper_temperature_bound=bool(np.any(expand(result.x)[n:]>upper[n:]-1e-4))))
                continue
            actual=model.physical_rhs(0.,x) if hasattr(model,'physical_rhs') else model.rhs(0.,x)
            dynamic=float(max(abs(actual[:model.motor_state_end])))
            heat_errors={m.id:[float(actual[bank.addresses[i].start]*m.winding_thermal.stator_capacity_j_per_k),float(actual[bank.addresses[i].start+1]*m.winding_thermal.rotor_capacity_j_per_k)] for i,m in enumerate(motors)}
            if any(max(abs(v) for v in heat_errors[m.id])>max(1e-7,1e-10*m.rated_va) for m in motors):
                rejected.append(dict(reason='stationary_heat_power_residual_w',errors=heat_errors));continue
            v_dynamic=model.electrical(x)[0];kcl=float(max(abs(operator(model,x)[0]@v_dynamic-model.network_injection(x))))
            voltage_error=float(max(abs(v_dynamic-voltage)))
            if dynamic>p.tolerance or kcl>p.tolerance or voltage_error>p.tolerance:
                rejected.append(dict(reason='original_ODE_or_KCL_residual',maximum=dynamic));continue
            if any(x[address]>=motors[i].thermal.trip_level for i,address in model.thermal_indices.items()):
                return None,dict(reason='initial_thermal_relay_limit')
            dimension=model.motor_state_end
            spectrum=bank.actions(0.,x,np.eye(dimension),model.electrical(x))
            eigenvalues=np.linalg.eigvals(spectrum);alpha=float(max(eigenvalues.real))
            if not np.isfinite(eigenvalues).all() or require_stable and alpha>=-1e-9:
                rejected.append(dict(reason='unstable_or_near_axis_electrothermal_equilibrium',spectral_abscissa_per_s=alpha));continue
            return x.copy(),dict(mode='motor_only_electrothermal_equilibrium',winding_state='equilibrium',thermal_state=p.thermal_state,
                evaluations=calls,analytic_reduced_jacobian=True,stationary_heat_balance_error_w=heat_errors,maximum_stationary_heat_error_w=max(abs(v) for values in heat_errors.values() for v in values),checked_dynamic_residual_per_s=dynamic,network_kcl_residual_pu=kcl,
                static_dynamic_voltage_mismatch_pu=voltage_error,full_physical_spectral_abscissa_per_s=alpha,
                physical_eigenvalues_per_s=[[float(e.real),float(e.imag)] for e in eigenvalues],
                stationary_stability_scope='connected motor EMF/speed/winding temperatures/I2 replica at fixed topology; no hybrid relay/contactor stability certificate',
                generator_constraints=0,motor_initial={m.id:dict(speed_pu=float(x[3*i+2]),current_pu=float(abs(current[i])),
                    stator_temperature_c=float(x[bank.addresses[i].start]),rotor_temperature_c=float(x[bank.addresses[i].start+1])) for i,m in enumerate(motors)},
                initial_temperature_policy='passport temperatures are bounded search seeds, not imposed stationary values',
                branch_note='bounded local multi-start search; no uniqueness/global nonexistence proof')
    except (ValueError,RuntimeError,ArithmeticError,np.linalg.LinAlgError) as exc:
        return None,dict(reason='electrothermal_equilibrium_search_failed',error=str(exc),evaluations=calls)
    return None,dict(reason='no_qualified_electrothermal_equilibrium',evaluations=calls,best_residual=best if np.isfinite(best) else None,
                     rejected_candidates=rejected,scope='bounded high-speed/temperature search, not a global nonexistence proof')
