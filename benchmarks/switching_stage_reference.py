"""Independent variable-size discrete-gradient stage oracle in mixed unknowns.

No native RHS/stamp, flux controller, or PWM implementation is imported.
The oracle writes KCL/KVL, winding flux and shaft equations directly and solves
all unknowns together with SciPy root, unlike production Norton elimination.
It is a stage oracle, not a full independently controlled trajectory benchmark.
"""
import math
import numpy as np
from scipy.optimize import root, least_squares


def solve_stage(parameters,state,t,h,gates,bypassed,brake_latched=False):
    p=parameters;m=p['motor'];lm=m['unsaturated_h'];ll=m['cages'][0]['leakage_h'];rr=m['cages'][0]['resistance_ohm'];rs=m['stator_r_ohm']
    # Unknowns: node midpoint voltages 8, line midpoint currents 3, stator and
    # rotor midpoint flux real/imag 4, midpoint shaft speed 1.
    count=len(m['cages']);leakage=np.array([c['leakage_h'] for c in m['cages']]);rotor_r=np.array([c['resistance_ohm'] for c in m['cages']]);size=2*(count+1)
    old_phi=np.asarray(state['flux']);old_i=np.asarray(state['line']);old_v=np.asarray(state['voltage']);old_w=state['speed'];temp=np.asarray(state['temperature'])
    thermal=p['thermal'];factor=1+thermal['resistance_alpha_per_k']*(temp-25.)
    rc=p['mosfet_resistance_ohm']*factor[1];rd=p['body_diode_resistance_ohm']*factor[1];rb=p['rectifier_resistance_ohm']*factor[0]
    env=np.asarray(p['grid_envelope']);scale=np.interp(t+h/2,env[:,0],env[:,1]);source=math.sqrt(2)*p['grid_ln_rms_v']*scale*np.cos(2*math.pi*p['grid_frequency_hz']*(t+h/2)+np.array([0.,-2*math.pi/3,2*math.pi/3]))
    winding=m.get('thermal');old_winding=np.array(state.get('winding_temperature',p.get('motor_initial_temperature_c') or [winding['ambient_c']]*2)) if winding else np.zeros(0)
    rpre=0. if bypassed else p['precharge_resistance_per_phase_ohm']
    vdc_old=old_v[3]-old_v[4];brake=0. if brake_latched else min(1.,max(0.,(vdc_old-p['brake_start_v'])/(p['brake_full_v']-p['brake_start_v'])))
    baseline=np.r_[old_v,old_i,old_phi,old_w,old_winding]
    def equations(increment,details=False):
        z=baseline+increment
        v=z[:8];i=z[8:11];ps=complex(*z[11:13]);rotor=z[13:11+size].reshape(count,2);pr=rotor[:,0]+1j*rotor[:,1];w=z[11+size]
        rstat=rs;rrot=rotor_r
        if winding:
            theta=z[12+size:14+size];factor_w=1+winding['alpha_per_c']*(theta-winding['reference_c']);rstat=rs*factor_w[0];rrot=rotor_r*factor_w[1]
        ir=(pr-ps)/leakage
        # Independent radial energy divided difference in extended precision;
        # the production implementation instead uses an integer power sum.
        before=np.array(old_phi[:2],dtype=np.longdouble);after=before+2*np.array(increment[11:13],dtype=np.longdouble)
        r0=np.linalg.norm(before);r1=np.linalg.norm(after);delta=r1*r1-r0*r0;exponent=m['saturation_exponent'];beta=m['saturation_beta_per_vs']
        if abs(delta)<1e-13*max(1.,r0*r0,r1*r1):coefficient=(1+(beta*((r0+r1)/2))**exponent)/(2*lm)
        else:coefficient=(.5+beta**exponent*(r1**(exponent+2)-r0**(exponent+2))/((exponent+2)*delta))/lm
        force=2*ps*float(coefficient)-sum(ir);ds_rate=2/h*complex(*increment[11:13]);is_=force+m.get('core_loss_s',0.)*ds_rate
        phase=np.array([is_.real,(-.5*is_+complex(0.,-math.sqrt(3)/2)*is_).real,(-.5*is_+complex(0.,math.sqrt(3)/2)*is_).real])
        up=np.maximum(0.,(v[:3]-v[3]-p['rectifier_drop_v'])/rb);low=np.maximum(0.,(v[4]-v[:3]-p['rectifier_drop_v'])/rb)
        top=np.where(gates[:,0],(v[3]-v[5:])/rc,0.);bottom=np.where(gates[:,1],(v[5:]-v[4])/rc,0.)
        body_up=np.maximum(0.,(v[5:]-v[3]-p['body_diode_drop_v'])/rd);body_low=np.maximum(0.,(v[4]-v[5:]-p['body_diode_drop_v'])/rd)
        vdc=v[3]-v[4];cap=p['dc_capacitance_f']*2/h*(increment[3]-increment[4]);earth=p['rail_earth_capacitance_f']*2/h*increment[3:5];bleed=vdc/p['bleed_ohm'];ibrake=brake*vdc/p['brake_resistance_ohm']
        kcl_ac=up-low-i
        kcl_plus=cap+earth[0]+bleed+ibrake-sum(up)+sum(top)-sum(body_up)
        kcl_minus=-cap+earth[1]-bleed-ibrake+sum(low)-sum(bottom)+sum(body_low)
        kcl_motor=phase-top+bottom+body_up-body_low
        di=2/h*p['source_inductance_h']*increment[8:11]-source+v[:3]+(p['source_resistance_ohm']+rpre)*i
        u=2/3*complex(v[5]-.5*v[6]-.5*v[7],math.sqrt(3)/2*(v[6]-v[7]))
        ds=ds_rate-u+rstat*is_
        old_rotor=old_phi[2:].reshape(count,2);old_pr=old_rotor[:,0]+1j*old_rotor[:,1]
        delta_r=increment[13:11+size].reshape(count,2)
        dr=2/h*(delta_r[:,0]+1j*delta_r[:,1])+rrot*ir-complex(0.,m['pole_pairs']*w)*pr
        torque=1.5*m['pole_pairs']*(force*ps.conjugate()).imag
        load=p['load_torque_nm']+m['fan_nm_per_rad_s2']*w*abs(w)+m['viscous_nm_per_rad_s']*w
        dw=2/h*m['inertia_kg_m2']*increment[11+size]-torque+load
        residual=np.r_[kcl_ac,kcl_plus,kcl_minus,kcl_motor,di,ds.real,ds.imag,np.column_stack([dr.real,dr.imag]).ravel(),dw]
        if winding:
            cs=winding['stator_capacity_j_per_k'];cr=winding['rotor_capacity_j_per_k'];gs=winding['stator_ambient_w_per_k'];gr=winding['rotor_ambient_w_per_k'];gx=winding['cross_w_per_k'];ambient=winding['ambient_c']
            ps_heat=1.5*(rstat*abs(is_)**2+m.get('core_loss_s',0.)*abs(ds_rate)**2);pr_heat=1.5*sum(rrot*abs(ir)**2)
            thermal_residual=[2/h*cs*increment[12+size]-ps_heat+gs*(theta[0]-ambient)+gx*(theta[0]-theta[1]),2/h*cr*increment[13+size]-pr_heat+gr*(theta[1]-ambient)-gx*(theta[0]-theta[1])]
            residual=np.r_[residual,thermal_residual]
        return (residual,torque) if details else residual
    guess_voltage=old_v.copy()
    for i in range(3):
        guess_voltage[i]=np.clip(source[i],old_v[4]-p['rectifier_drop_v'],old_v[3]+p['rectifier_drop_v']) if abs(old_i[i])<1e-10 else old_v[3]+p['rectifier_drop_v']+rb*old_i[i] if old_i[i]>0 else old_v[4]-p['rectifier_drop_v']+rb*old_i[i]
    old_ps=complex(*old_phi[:2]);old_rotor=old_phi[2:].reshape(count,2);old_pr=old_rotor[:,0]+1j*old_rotor[:,1];is_old=(old_ps*(1+(m['saturation_beta_per_vs']*abs(old_ps))**m['saturation_exponent'])/lm-sum((old_pr-old_ps)/leakage))/(1+rs*m.get('core_loss_s',0.))
    phase_old=[is_old.real,(-.5*is_old-1j*math.sqrt(3)/2*is_old).real,(-.5*is_old+1j*math.sqrt(3)/2*is_old).real]
    for i in range(3):
        guess_voltage[5+i]=old_v[3]-rc*phase_old[i] if gates[i,0] else old_v[4]-rc*phase_old[i] if gates[i,1] else old_v[4]-p['body_diode_drop_v']-rd*phase_old[i] if phase_old[i]>0 else old_v[3]+p['body_diode_drop_v']-rd*phase_old[i]
    if max(abs(np.asarray(phase_old)))<1e-7 and not (np.any(gates[:,0]) and np.any(gates[:,1])):
        # At an unenergized isolated star, the common-mode potential is a
        # gauge. Tiny roundoff currents must not seed opposite diode clamps.
        # This changes only the initial guess, never currents or equations.
        guess_voltage[5:]=old_v[3] if np.any(gates[:,0]) else old_v[4] if np.any(gates[:,1]) else np.mean(old_v[3:5])
    guess=np.r_[guess_voltage,old_i,old_phi,old_w,old_winding]
    variable_scale=np.r_[np.full(8,500.),np.full(3,10.),np.full(size,.5),100.,np.full(len(old_winding),100.)]
    equation_scale=np.r_[np.ones(8),np.full(3,500.),np.full(size,500.),10.,np.full(len(old_winding),500.)]
    def scaled(y):return equations(y*variable_scale)/equation_scale
    # A fixed-rail scalar diode solve supplies a consistent source-current
    # predictor through commutation, rather than reusing the old active set.
    impedance=2*p['source_inductance_h']/h+p['source_resistance_ohm']+rpre
    norton=(2*p['source_inductance_h']/h*old_i+source)/impedance
    predicted_ac=np.empty(3)
    for phase in range(3):
        upper=old_v[3]+p['rectifier_drop_v'];lower=old_v[4]-p['rectifier_drop_v']
        free=norton[phase]*impedance
        rail=upper if free>upper else lower if free<lower else None
        predicted_ac[phase]=free if rail is None else (norton[phase]+rail/rb)/(1/impedance+1/rb)
    candidates=[]
    for ac_guess in [predicted_ac,guess[:3],np.zeros(3),source]:
        initial=guess.copy();initial[:3]=ac_guess;initial[8:11]=norton-ac_guess/impedance
        sol=root(scaled,(initial-baseline)/variable_scale,method='hybr',options={'xtol':1e-11,'maxfev':3000})
        if max(abs(equations(sol.x*variable_scale)))>2e-6:
            sol=root(scaled,sol.x,method='lm',options={'ftol':1e-13,'xtol':1e-13,'gtol':1e-13,'maxiter':5000})
            second=root(scaled,sol.x,method='hybr',options={'xtol':1e-11,'maxfev':3000})
            if np.max(abs(scaled(second.x)))<np.max(abs(scaled(sol.x))):sol=second
        candidates.append(sol.x*variable_scale)
        if max(abs(equations(candidates[-1])))<2e-6:break
    chosen=min(candidates,key=lambda z:np.max(abs(equations(z))))
    if max(abs(equations(chosen)))>2e-6:
        # Gate-off common-mode gauges and tiny commutation intervals can make
        # a square root iteration stall. A rank-tolerant residual minimization
        # is a fallback only; the physical residual acceptance is unchanged.
        def raw(y):return equations(y*variable_scale)
        def absolute_jacobian(y):
            # Relative differences of tiny increments vanish in the residual.
            # Use an absolute perturbation in normalized coordinates instead.
            result=np.empty((len(y),len(y)))
            for column in range(len(y)):
                step=1e-9*max(1.,abs(y[column]));direction=np.zeros(len(y));direction[column]=step
                result[:,column]=(raw(y+direction)-raw(y-direction))/(2*step)
            return result
        fallback=least_squares(raw,chosen/variable_scale,jac=absolute_jacobian,x_scale='jac',xtol=1e-13,ftol=1e-13,gtol=1e-13,max_nfev=2000)
        if max(abs(equations(fallback.x*variable_scale)))<max(abs(equations(chosen))):chosen=fallback.x*variable_scale
    residual,torque=equations(chosen,True)
    if max(abs(residual))>2e-6:raise ValueError(f'independent midpoint circuit residual exceeds bound: {max(abs(residual)):.9g}, row {int(np.argmax(abs(residual)))}')
    physical=baseline+chosen
    return dict(voltage=physical[:8].tolist(),line_mid=physical[8:11].tolist(),flux_mid=physical[11:11+size].tolist(),speed_mid=float(physical[11+size]),winding_temperature_mid_c=physical[12+size:].tolist(),torque_nm=float(torque),residual_max=float(max(abs(residual))),rail_increment_mid=chosen[3:5].tolist(),line_increment_mid=chosen[8:11].tolist(),flux_increment_mid=chosen[11:11+size].tolist(),speed_increment_mid=float(chosen[11+size]))
