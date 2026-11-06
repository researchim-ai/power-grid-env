"""Index-1 balanced-network DAE with explicit algebraic voltage constraints.

F = [xdot - f(x,V), Re(Y V-b(x)), Im(Y V-b(x))]. Y is nonsingular
inside the declared topology. Projection is exact up to the sparse LU solve.
This formulation uses physical differential equations at a supplied voltage,
not an RHS which silently projects an inconsistent algebraic input.
"""
from dataclasses import dataclass
import numpy as np


def mode_token(model):
    arrays=tuple((name,np.asarray(getattr(model,name)).tobytes()) for name in
                 ['connected','thermal_latched','load_scale','gen_connected','gen_mechanical','gen_voltage'] if hasattr(model,name))
    controls=getattr(model,'controllers',None)
    control_arrays=tuple((name,tuple(sorted(getattr(controls,name).items())) if isinstance(getattr(controls,name),dict) else np.asarray(getattr(controls,name)).tobytes()) for name in ['pref','vref'] if hasattr(controls,name))
    boundary=tuple(sorted(getattr(getattr(model,'controller_boundaries',None),'active',{}).items()))
    field=np.asarray(getattr(getattr(model,'field_thermal',None),'field_enabled',[])).tobytes()
    uel=tuple(sorted(getattr(getattr(model,'underexcitation_limiters',None),'regions',{}).items()))
    oel=tuple((i,tuple(sorted(k.context.items()))) for i,(_,_,k) in getattr(getattr(model,'oel2c',None),'entries',{}).items())
    automation=getattr(model,'automation',None)
    automation_mode=None if automation is None else (
        bool(automation.initialized),bool(model.source_connected),tuple(sorted(model.lines.closed.items())),
        tuple(automation.active),tuple(automation.tripped),tuple(automation.ats_active),tuple(automation.ats_latched),
        tuple(sorted(automation.available.items())),tuple(sorted(automation.manual_inhibit)),tuple(sorted(automation.queue)))
    return model.lu,float(model.source_scale),arrays,control_arrays,boundary,field,uel,oel,automation_mode


@dataclass(frozen=True)
class ConsistentState:
    state: np.ndarray
    derivative: np.ndarray
    voltage: np.ndarray
    maximum_kcl_pu: float
    maximum_differential_residual: float


class NetworkDAE:
    def __init__(self,model):
        self.model=model;self.mode=mode_token(model);self.nodes=len(model.bus_names);self.dimension=getattr(model,'dynamic_end',getattr(getattr(model,'takeover_oel',None),'end',model.motor_state_end))

    def check_mode(self):
        if self.mode!=mode_token(self.model):raise ValueError('DAE topology/context changed; construct a new mode')

    def project(self,state):
        self.check_mode();state=np.asarray(state)
        if state.shape!=(self.dimension,) or not np.isrealobj(state) or not np.isfinite(state).all():raise ValueError('finite real dynamic state required')
        v=self.model.lu.solve(self.model.network_injection(state))
        if not np.isfinite(v).all():raise ValueError('nonfinite algebraic projection')
        return v

    def residual(self,time,state,derivative,voltage):
        self.check_mode();state=np.asarray(state);derivative=np.asarray(derivative);voltage=np.asarray(voltage)
        if state.shape!=(self.dimension,) or derivative.shape!=state.shape or voltage.shape!=(self.nodes,) or not np.isrealobj(state) or not np.isrealobj(derivative) or not np.isfinite(state).all() or not np.isfinite(derivative).all() or not np.isfinite(voltage).all() or not np.isfinite(time):raise ValueError('invalid DAE residual arguments')
        dynamic=derivative-self.model.differential_at_voltage(time,state,voltage)
        network=self.model.matrix@voltage-self.model.network_injection(state)
        return np.r_[dynamic,network.real,network.imag]

    def consistent(self,time,state,*,maximum_kcl_pu=1e-8):
        if not np.isfinite(maximum_kcl_pu) or maximum_kcl_pu<=0:raise ValueError('positive finite DAE consistency tolerance required')
        x=np.asarray(state).copy();v=self.project(x);f=self.model.differential_at_voltage(time,x,v)
        r=self.residual(time,x,f,v);kcl=float(np.max(abs(self.model.matrix@v-self.model.network_injection(x))))
        if not np.isfinite(f).all() or kcl>maximum_kcl_pu:raise ValueError('DAE consistency failed')
        return ConsistentState(x,f.copy(),v.copy(),kcl,float(np.max(abs(r[:len(x)]))))


def injection_product(model,state,directions):
    """Analytic b_x dx for motor and GENCLS/GENROU Norton sources."""
    from power_grid_env.genrou_jacobian import clip_slope
    u=np.asarray(directions);state=np.asarray(state)
    if u.ndim!=2 or u.shape[0]!=len(state) or not np.isrealobj(u) or not np.isfinite(u).all():raise ValueError('invalid DAE injection directions')
    n=len(model.bus_names);width=u.shape[1];result=np.zeros((n,width),complex)
    indices=np.arange(len(model.settings.motors))*3
    np.add.at(result,model.motor_buses,model.motor_y[:,None]*(u[indices]+1j*u[indices+1]))
    if not hasattr(model,'generators'):return result
    eg=model.generator_internal(state)
    for i,m in enumerate(model.gen_passports):
        delta=model.gen_offset+2*i;de=1j*eg[i]*u[delta]
        if i in model.round_rotors.addresses:
            a=model.round_rotors.addresses[i];gd1,gd2,gq1,_,_=model.round_rotors.coefficients[i]
            dpd=gd1*u[a.start]+gd2*(m.x_d_prime_pu-m.leakage_pu)*u[a.start+2]
            dpq=gq1*u[a.start+1]+(1-gq1)*u[a.start+3]
            de=de+(dpd-1j*dpq)*np.exp(1j*state[delta])
        elif i in model.controllers.avr:
            _,field=model.controllers.avr[i];p=m.exciter
            de=de+clip_slope(state[field],p.EMIN,p.EMAX)*u[field]*np.exp(1j*state[delta])
        result[model.gen_buses[i]]+=model.active_gen_y[i]*de
    return result


def residual_product(system,time,state,derivative,voltage,dx,dxdot,dv):
    """Full augmented residual action (dxdot - f_x dx - f_V dV, Y dV - b_x dx).

    Supports inconsistent algebraic iterates, fixed discrete branches and real
    physical state directions. No finite-difference or eliminated-voltage
    substitution is used to stand in for a supplied algebraic direction.
    """
    system.residual(time,state,derivative,voltage)
    x=np.asarray(state);u=np.asarray(dx);udot=np.asarray(dxdot);vdir=np.asarray(dv)
    if u.shape!=x.shape or udot.shape!=x.shape or vdir.shape!=(system.nodes,) or not np.isrealobj(u) or not np.isrealobj(udot) or not np.isfinite(u).all() or not np.isfinite(udot).all() or not np.isfinite(vdir).all():raise ValueError('invalid augmented DAE tangent')
    m=system.model;cb=m.integration_jacobian()
    if not callable(getattr(cb,'dae_actions',None)):raise ValueError('analytic partial DAE callback required')
    ctx=m.electrical_at_voltage(x,np.asarray(voltage))
    df=cb.dae_actions(time,x,u[:,None],ctx,voltage_directions=vdir[:,None])[:,0]
    dr=m.matrix@vdir-injection_product(m,x,u[:,None])[:,0]
    result=np.r_[udot-df,dr.real,dr.imag]
    if not np.isfinite(result).all():raise ValueError('nonfinite augmented DAE tangent')
    return result
