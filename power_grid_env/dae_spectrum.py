"""Bounded critical-mode extraction from an analytic network Schur operator."""
import numpy as np
from scipy.sparse.linalg import LinearOperator,eigs,ArpackNoConvergence
from power_grid_env.dae import NetworkDAE,mode_token
from power_grid_env.dae_linearization import DAEBlocks
from power_grid_env.evidence import package_fingerprint


def critical_modes(model,state,*,indices=None,count=6,tolerance=1e-8,maximum_iterations=4000,maximum_products=100000,equilibrium_tolerance=1e-7):
    x=np.asarray(state);system=NetworkDAE(model);initial=system.consistent(0.,x);selected=np.arange(len(x)) if indices is None else np.asarray(indices)
    if selected.ndim!=1 or selected.dtype.kind not in 'iu' or len(set(selected.tolist()))!=len(selected) or np.any(selected<0) or np.any(selected>=len(x)):raise ValueError('unique in-range dynamic mode indices required')
    if type(count) is not int or not 1<=count<len(selected)-1:raise ValueError('critical-mode count must be in 1..active_dimension-2')
    if not np.isfinite([tolerance,equilibrium_tolerance]).all() or tolerance<=0 or equilibrium_tolerance<=0 or type(maximum_iterations) is not int or maximum_iterations<1 or type(maximum_products) is not int or maximum_products<1:raise ValueError('positive finite spectrum budgets required')
    if max(abs(initial.derivative[selected]))>equilibrium_tolerance:raise ValueError('selected subsystem is not at the declared equilibrium tolerance')
    token=mode_token(model);calls=0
    def check():
        if mode_token(model)!=token:raise ValueError('spectral topology/context changed')
    cb=model.integration_jacobian()
    if not callable(getattr(cb,'dae_actions',None)):raise ValueError('analytic network DAE spectrum required')
    blocks=DAEBlocks(model,cb,0.,x,32,check,lambda:None)
    def action(v):
        nonlocal calls;calls+=1;check()
        if calls>maximum_products:raise ValueError('critical-mode product budget exceeded')
        full=np.zeros(len(x),dtype=np.asarray(v).dtype);full[selected]=np.asarray(v).ravel()
        return blocks.action(full)[selected]
    op=LinearOperator((len(selected),len(selected)),matvec=action,dtype=float)
    try:values,vectors=eigs(op,k=count,which='LR',tol=tolerance,maxiter=maximum_iterations,v0=np.linspace(1.,2.,len(selected)))
    except ArpackNoConvergence as exc:raise ValueError('critical-mode iteration did not converge; partial spectrum is not qualified') from exc
    order=np.argsort(values.real)[::-1];values=values[order];vectors=vectors[:,order];residuals=[]
    for value,vector in zip(values,vectors.T):
        applied=action(vector);error=float(np.linalg.norm(applied-value*vector)/max(np.linalg.norm(applied),abs(value)*np.linalg.norm(vector),1.))
        if not np.isfinite(error) or error>max(1e-7,10*tolerance):raise ValueError('critical-mode eigenpair residual failed')
        residuals.append(error)
    return dict(completed=True,code_sha256=package_fingerprint(),method='analytic_Schur_ARPACK_LR',active_indices=selected.tolist(),eigenvalues=[dict(real=float(v.real),imag=float(v.imag)) for v in values],eigenpair_relative_residuals=residuals,products=calls,maximum_iterations=maximum_iterations,maximum_products=maximum_products,maximum_computed_real_part=float(max(values.real)),maximum_kcl_pu=initial.maximum_kcl_pu,scope='computed largest-real modes of an explicitly selected equilibrium subsystem; no global nonlinear or hybrid stability claim',global_or_hybrid_stability_certified=False)
