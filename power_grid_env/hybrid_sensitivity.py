"""Forward variational derivatives along the actual RMS event trajectory.

Initial physical-state directions; algebraic voltages are eliminated using the
existing analytic network Jacobian. Scheduled identity resets preserve S;
thermal trips use saltation. Ambiguous guard cohorts are explicitly rejected.
"""
import numpy as np
from scipy.integrate import solve_ivp
from power_grid_env.hybrid_events import saltation_product


class TrajectorySensitivity:
    def __init__(self, directions):
        a=np.asarray(directions)
        if a.ndim==1:a=a[:,None]
        if a.ndim!=2 or not a.size or a.shape[1]>32 or not np.isrealobj(a) or not np.isfinite(a).all():
            raise ValueError('finite nonempty real initial-state direction matrix required')
        self.value=a.astype(float,copy=True);self.initial_directions=self.value.copy();self.events=[];self.failure=None

    def start(self,model,state):
        # Controller resets require their own reset derivatives; never silently
        # pass an identity through an unsupported model.
        from power_grid_env.induction_network import InductionNetwork
        if type(model) is not InductionNetwork or any(m.contactor for m in model.settings.motors):
            raise ValueError('trajectory sensitivity currently requires induction network without automatic contactors')
        if len(state)!=len(self.value):raise ValueError('direction dimension differs from physical initial state')
        self.callback=model.integration_jacobian()
        if self.callback is None:raise ValueError('analytic network Jacobian required')
        self.shape=self.value.shape;self.initial_state=np.array(state,copy=True)

    def segment(self,model,solution):
        if self.failure or solution.t[-1]==solution.t[0]:return
        from power_grid_env.motor_jacobian import columns
        def rhs(t,y):
            model._sensitivity_budget.check_time()
            return columns(model,solution.sol(t),None,directions=y.reshape(self.shape)).ravel()
        s=solve_ivp(model._sensitivity_budget.rhs(rhs),(solution.t[0],solution.t[-1]),self.value.ravel(),method='DOP853',
                    rtol=model.settings.rtol,atol=model.settings.atol,max_step=model.settings.step_s)
        if not s.success or not np.isfinite(s.y).all():
            self.failure='variational_integration_failed';return
        self.value=s.y[:,-1].reshape(self.shape)

    def before_events(self,model,time,state,triggered,scheduled,control_boundary):
        self.pending=None
        if self.failure:return
        if control_boundary:
            self.failure='unsupported_control_reset';return
        if len(triggered)>1 or (triggered and scheduled):
            self.failure='coincident_independent_guards_or_scheduled_boundary';return
        if triggered:
            i=next(iter(triggered));normal=np.zeros(len(state));normal[model.thermal_indices[i]]=1.
            self.pending=(normal,model.rhs(time,state).copy())

    def after_events(self,model,time,state,event_log):
        if self.failure:return
        if self.pending:
            normal,before=self.pending
            try:
                self.value=np.column_stack([saltation_product(normal,before,model.rhs(time,state),v) for v in self.value.T])
            except ValueError as exc:self.failure=str(exc);return
        self.events=[dict(time_s=e['time_s'],kind=e['kind'],equipment_id=e.get('equipment_id'),outcome=e.get('outcome')) for e in event_log]
        self.pending=None

    def report(self,completed):
        ok=completed and self.failure is None
        return dict(available=ok,reason=self.failure if self.failure else (None if completed else 'physical_study_incomplete'),
                    initial_state=getattr(self,'initial_state',np.array([])).tolist(),initial_state_directions=self.initial_directions.tolist(),
                    final_state_directions=self.value.tolist() if ok else None,event_sequence=self.events,
                    coordinates='physical RMS initial states; fixed scheduled times; thermal saltation',
                    scope='induction network, no automatic contactors; independent simultaneous guards rejected')


def run_initial_sensitivity(network,settings,directions,*,initial_offset=None):
    """Run primary physical trajectory and tangent columns in the same event loop.

    Directions describe perturbations after operating-point initialization, not
    derivatives of an equilibrium solve or of arbitrary equipment parameters.
    """
    from power_grid_env.induction_network import InductionNetwork,validate_induction,integrate_induction_model
    from power_grid_env.motor_operating_point import initialize_motor_state
    scope=validate_induction(network,settings)
    if not scope['ok']:raise ValueError(scope)
    model=InductionNetwork(network,settings);state,op=initialize_motor_state(model)
    if state is None:raise ValueError(op)
    if initial_offset is not None:
        offset=np.asarray(initial_offset)
        if offset.shape!=state.shape or not np.isrealobj(offset) or not np.isfinite(offset).all():raise ValueError('invalid initial offset')
        state=state+offset
    observer=TrajectorySensitivity(directions)
    return integrate_induction_model(model,scope,state,op,observer=observer)


def audit_initial_sensitivity(network,settings,direction,*,steps=(4e-5,2e-5,1e-5),rtol=5e-4,atol=2e-6):
    """Independent complete-study central differences, including event topology.

    A passed finite-difference audit is local numerical evidence, not proof of
    differentiability throughout a parameter interval.
    """
    from power_grid_env.induction_network import InductionNetwork,validate_induction,integrate_induction_model
    from power_grid_env.motor_operating_point import initialize_motor_state
    raw=np.asarray(direction)
    if not np.isrealobj(raw):raise ValueError('real initial-state direction required')
    v=np.asarray(raw,dtype=float)
    hs=np.asarray(steps,dtype=float)
    if v.ndim!=1 or not np.isfinite(v).all() or not np.any(v):raise ValueError('nonzero finite real direction required')
    if hs.shape!=(3,) or not np.isfinite(hs).all() or np.any(hs<=0) or not np.all(np.diff(hs)<0):raise ValueError('three decreasing positive difference steps required')
    if not np.isfinite([rtol,atol]).all() or min(rtol,atol)<=0:raise ValueError('positive finite audit tolerances required')
    base=run_initial_sensitivity(network,settings,v);sensitivity=base.get('sensitivity',{})
    if not sensitivity.get('available'):return dict(passed=False,reason=sensitivity.get('reason','incomplete'),baseline=base)
    target=np.array(sensitivity['final_state_directions'])[:,0]
    signature=lambda r:[(e['kind'],e.get('equipment_id'),e.get('outcome')) for e in r.get('event_log',[])]
    rows=[]
    for h in hs:
        states=[]
        for sign in (1,-1):
            m=InductionNetwork(network,settings);x,op=initialize_motor_state(m)
            if x is None:raise ValueError(op)
            # Retain the final full physical state with a minimal endpoint hook;
            # no variational equations or saltation enter these peer runs.
            class Endpoint:
                def start(self,*args):pass
                def segment(self,m,sol):self.state=sol.y[:,-1].copy()
                def before_events(self,*args):pass
                def after_events(self,m,t,x,*args):self.state=x.copy()
                def report(self,*args):return {}
            endpoint=Endpoint()
            peer=integrate_induction_model(m,validate_induction(network,settings),x+sign*h*v,op,observer=endpoint)
            if not peer.get('completed'):return dict(passed=False,reason='finite_difference_study_incomplete',steps=rows)
            if signature(peer)!=signature(base):return dict(passed=False,reason='event_sequence_changed',steps=rows)
            states.append(endpoint.state)
        fd=(states[0]-states[1])/(2*h);error=abs(target-fd)
        rows.append(dict(step=float(h),maximum_absolute_error=float(max(error)),directions=fd.tolist(),passed=bool(np.all(error<=atol+rtol*abs(target)))))
    return dict(passed=all(row['passed'] for row in rows),reason=None,steps=rows,
                final_state_direction=target.tolist(),event_sequence=signature(base),local_numerical_evidence=True)
