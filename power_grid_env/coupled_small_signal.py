"""Bounded local eigenanalysis of mixed machine/motor/controller equilibria."""
import numpy as np
from power_grid_env.coupled_rms import CoupledNetwork,validate_coupled
from power_grid_env.coupled_initialization import initialize_coupled
from power_grid_env.coupled_linearization import reduced_jacobian
from power_grid_env.small_signal import analyze_matrix
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash

MAX_STATES=128


def run_coupled_small_signal(network,settings):
    scope=validate_coupled(network,settings)
    def failure(reason,status='rejected',**extra):return dict(solved=False,completed=False,status=status,reason=reason,**extra)
    if not scope['ok']:return failure('coupled_scope',scope=scope)
    if any(m.contactor for m in settings.motors):return failure('hybrid_contactor_modal_analysis_unsupported')
    if settings.events or settings.faults or settings.operating_point is None:return failure('coupled_small_signal_equilibrium_only')
    count=sum((5 if m.model=='double_cage' else 3) for m in settings.motors if m.connected)+sum((6 if m.model=='GENROU' else 2)+2*bool(m.governor)+2*bool(m.exciter)+7*bool(m.stabilizer) for m in settings.machines)
    if count>MAX_STATES:return failure('coupled_small_signal_state_budget',maximum_states=MAX_STATES)
    try:
        model=CoupledNetwork(network,settings);state,initial=initialize_coupled(model,require_stable=False)
        if state is None:return failure(initial['reason'],'initialization_failed',operating_point=initial)
        thermal=initial['thermal_residual_per_s']
        if any(abs(v)>settings.operating_point.tolerance for v in thermal.values()):return failure('nonstationary_thermal_state')
        coarse,names=reduced_jacobian(model,state,1e-6);fine,_=reduced_jacobian(model,state,5e-7)
        discrepancy=float(np.linalg.norm(coarse-fine,ord=np.inf)/max(1.,np.linalg.norm(fine,ord=np.inf)))
        if not np.isfinite(discrepancy) or discrepancy>1e-5:return failure('linearization_step_not_converged','linearization_failed',relative_matrix_change=discrepancy)
        analyzed=analyze_matrix(fine,names)
    except (ValueError,RuntimeError,ArithmeticError,np.linalg.LinAlgError) as exc:return failure('coupled_linearization','linearization_failed',error=str(exc))
    import scipy
    return dict(**analyzed,solved=True,completed=True,status='completed',study='coupled_small_signal',backend='scipy',backend_version=scipy.__version__,
        time_domain_simulated=False,state_names=names,state_matrix_per_s=fine.tolist(),operating_point=initial,
        relative_matrix_change=discrepancy,requested_relative_difference_steps=[1e-6,5e-7],branch_preserving_steps=True,scope=scope,
        excluded_states='Disconnected motor electrical/speed states and one-way thermal states; no hybrid-trip modal inference',
        linearization='Centered finite differences of original joint RHS after exact sparse network elimination; local interior branch only',
        settings=settings.model_dump(mode='json'),network_sha256=canonical_hash(network),code_sha256=package_fingerprint(),
        industrial_profile_qualified=False,field_validated=False,competitive_superiority_proven=False)
