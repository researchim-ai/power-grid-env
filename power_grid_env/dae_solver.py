"""Specialized index-1 network DAE: projected Radau IIA with Schur/GMRES.

The fixed-topology algebraic block Y is eliminated exactly at every stage.
The dynamic Schur complement is applied analytically, never assembled. SciPy's
pinned Radau implementation retains its collocation, error control, dense output
and event interface; only its linear algebra is replaced. No general mass-matrix
or singular/high-index DAE support is implied.
"""
import warnings
import numpy as np
import scipy
from scipy.integrate import Radau
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import LinearOperator,gmres
from scipy.linalg import lu_factor,lu_solve,LinAlgWarning
from power_grid_env.dae import NetworkDAE,mode_token
from power_grid_env.runtime_limits import RuntimeLimitExceeded


class LinearFailure(RuntimeError):pass




class JacobianSnapshot:
    def __init__(self,owner,time,state,*,reuse=False):
        self.owner=owner;self.time=time;self.state=state.copy();self.mode=mode_token(owner.model)
        self.n=len(state)
        from power_grid_env.dae_linearization import DAEBlocks
        cached=getattr(owner.model,'_dae_linearization_cache',None)
        if reuse and cached is not None and cached.mode==self.mode and cached.maximum==owner.options.block_size:
            self.coefficients=cached;owner.stats['reused_linearizations']+=1
        else:
            self.coefficients=DAEBlocks(owner.model,owner.callback,time,owner.coordinates.decode(time,self.state),owner.options.block_size,owner.check,lambda:owner.tick('jvp_evaluations',owner.options.maximum_jvp_evaluations))
            owner.model._dae_linearization_cache=self.coefficients;owner.stats['linearizations']+=1
        self.blocks=[(indices,owner.coordinates.block(time,indices,block)) for indices,block in self.coefficients.blocks]
        owner.stats['peak_local_linearization_scalar_slots']=max(owner.stats['peak_local_linearization_scalar_slots'],self.coefficients.stored_local_scalar_slots)
        owner.stats['maximum_preconditioner_block_size']=max(owner.stats['maximum_preconditioner_block_size'],max(len(i) for i,_ in self.blocks))

    def action(self,vector):
        o=self.owner;o.check()
        if mode_token(o.model)!=self.mode:raise ValueError('stale DAE Jacobian topology/context')
        o.tick('jvp_evaluations',o.options.maximum_jvp_evaluations)
        result=o.coordinates.encode(self.time,self.coefficients.action(o.coordinates.decode(self.time,vector)))-o.coordinates.rate(vector)
        if not np.isfinite(result).all():raise ValueError('nonfinite DAE Schur product')
        return result


class Identity:
    def __rmul__(self,shift):return Shift(shift)


class Shift:
    def __init__(self,value):self.value=value
    def __sub__(self,jacobian):return ShiftedSystem(self.value,jacobian)


class ShiftedSystem:
    def __init__(self,shift,jacobian):
        self.shift=shift;self.jacobian=jacobian;self.blocks=[];dtype=complex if np.iscomplexobj(shift) else float
        with warnings.catch_warnings():
            warnings.simplefilter('error',LinAlgWarning)
            try:
                for indices,block in jacobian.blocks:
                    self.blocks.append((indices,lu_factor(shift*np.eye(len(indices),dtype=dtype)-block)))
            except (LinAlgWarning,ValueError) as exc:raise LinearFailure('singular DAE block preconditioner') from exc
        self.operator=LinearOperator((jacobian.n,jacobian.n),matvec=self.action,dtype=dtype)
        self.preconditioner=LinearOperator((jacobian.n,jacobian.n),matvec=self.precondition,dtype=dtype)

    def action(self,v):
        v=np.asarray(v).reshape(self.jacobian.n)
        return self.shift*v-self.jacobian.action(v)

    def precondition(self,v):
        out=np.empty(np.shape(v),dtype=np.result_type(np.asarray(v).dtype,np.asarray(self.shift).dtype))
        for indices,factor in self.blocks:out[indices]=lu_solve(factor,v[indices])
        return out

    def solve(self,rhs):
        o=self.jacobian.owner;o.check();o.stats['linear_solves']+=1
        def iteration(value):o.tick('linear_iterations',o.options.maximum_linear_iterations)
        limit=max(o.options.krylov_atol,o.options.krylov_rtol*np.linalg.norm(rhs))
        candidate=self.precondition(rhs)
        candidate_residual=float(np.linalg.norm(self.action(candidate)-rhs))
        if np.isfinite(candidate).all() and candidate_residual<=limit:
            x,info,residual=candidate,0,candidate_residual
            o.stats['preconditioner_direct_solves']+=1
        else:
            x,info=gmres(self.operator,rhs,x0=candidate,M=self.preconditioner,rtol=o.options.krylov_rtol,atol=o.options.krylov_atol,
                         restart=min(o.options.krylov_restart,len(rhs)),maxiter=o.options.krylov_max_cycles,callback=iteration,callback_type='pr_norm')
            residual=float(np.linalg.norm(self.action(x)-rhs))
        o.stats['maximum_linear_residual_ratio']=max(o.stats['maximum_linear_residual_ratio'],residual/max(float(limit),np.finfo(float).tiny))
        o.stats['maximum_linear_relative_residual']=max(o.stats['maximum_linear_relative_residual'],residual/max(float(np.linalg.norm(rhs)),np.finfo(float).tiny))
        if info!=0 or not np.isfinite(x).all() or residual>limit*(1+1e-6):raise LinearFailure('DAE GMRES failed true residual check')
        return x


class NetworkRadau(Radau):
    def __init__(self,fun,t0,y0,t_bound,*,model,callback,options,stats,check_time,**kwargs):
        if scipy.__version__!='1.15.2':raise ValueError('network DAE backend requires validated SciPy 1.15.2')
        if not callable(getattr(callback,'jacobian_actions',None)) or not callable(getattr(callback,'dae_actions',None)):raise ValueError('DAE requires analytic Schur actions')
        self.model=model;self.callback=callback;self.options=options;self.stats=stats;self.check_time=check_time;self.mesh_scalars=0
        stats['segments']+=1
        from power_grid_env.dae_coordinates import RotatingMotorCoordinates
        self.coordinates=RotatingMotorCoordinates(model,t0,y0);model._dae_coordinates=self.coordinates
        stats['rotating_coordinate_segments']+=int(self.coordinates.active)
        physical_fun=fun
        def fun(time,state):return self.coordinates.encode(time,physical_fun(time,self.coordinates.decode(time,state)))-self.coordinates.rate(state)
        self.system=NetworkDAE(model);initial=self.system.consistent(t0,y0)
        stats['maximum_consistency_kcl_pu']=max(stats['maximum_consistency_kcl_pu'],initial.maximum_kcl_pu)
        kwargs.pop('jac',None)
        restart=getattr(model,'_dae_restart',None)
        reusable=restart is not None and restart['mode']==mode_token(model) and restart['time']==t0 and np.array_equal(restart['state'],y0)
        if reusable:kwargs['first_step']=min(restart['h_abs'],kwargs.get('max_step',np.inf),abs(t_bound-t0))
        # A sparse zero placeholder keeps the parent constructor O(n), without
        # invoking finite differences or allocating a dense state identity.
        super().__init__(fun,t0,y0,t_bound,jac=csc_matrix((len(y0),len(y0))),**kwargs)
        self.I=Identity();self.jac=self._snapshot;self.J=self._snapshot(t0,y0,self.f,reuse=True)
        self.current_jac=False
        self.lu=self._factor;self.solve_lu=lambda factor,b:factor.solve(b)
        if reusable:
            from power_grid_env.dae_coordinates import RestartInterpolant
            self.h_abs_old=restart['h_abs_old'];self.error_norm_old=restart['error_norm_old'];self.sol=RestartInterpolant(restart['last_dense'],restart['coordinates'],self.coordinates);stats['reused_step_history']+=1

    def check(self):self.check_time();self.system.check_mode()

    def tick(self,name,limit):
        self.check();value=self.stats[name]+1
        if value>limit:raise RuntimeLimitExceeded('dae_'+name,limit,value)
        self.stats[name]=value

    def _snapshot(self,t,y,f=None,*,reuse=False):
        self.njev+=1;return JacobianSnapshot(self,t,y,reuse=reuse)

    def _factor(self,operator):
        self.nlu+=1;self.stats['preconditioner_factorizations']+=1
        return operator

    def _step_impl(self):
        for retry in range(self.options.maximum_linear_retries+1):
            self.check()
            try:result=super()._step_impl()
            except LinearFailure as exc:
                self.stats['linear_retries']+=1
                if retry==self.options.maximum_linear_retries:return False,str(exc)
                self.h_abs*=.5;self.LU_real=None;self.LU_complex=None;self.J=self._snapshot(self.t,self.y,self.f);self.current_jac=True
                continue
            if result[0]:
                self.stats['accepted_steps']+=1
                self.stats['last_accepted_time_s']=float(self.t)
                self.mesh_scalars+=self.sol.Q.size+self.sol.y_old.size+self.y.size+2
                self.stats['peak_segment_retained_scalars']=max(self.stats['peak_segment_retained_scalars'],self.mesh_scalars)
                if self.mesh_scalars>self.options.maximum_segment_retained_scalars:raise RuntimeLimitExceeded('dae_segment_retained_scalars',self.options.maximum_segment_retained_scalars,self.mesh_scalars)
                physical_state=self.coordinates.decode(self.t,self.y)
                v=self.system.project(physical_state);kcl=float(np.max(abs(self.model.matrix@v-self.model.network_injection(physical_state))))
                self.stats['maximum_consistency_kcl_pu']=max(self.stats['maximum_consistency_kcl_pu'],kcl)
                if kcl>1e-8:return False,'DAE algebraic residual exceeded tolerance'
                self.model._dae_restart=dict(mode=mode_token(self.model),time=self.t,state=physical_state.copy(),coordinates=self.coordinates,h_abs=self.h_abs,h_abs_old=self.h_abs_old,error_norm_old=self.error_norm_old,last_dense=self.sol)
            return result
        return False,'DAE linear retry budget exhausted'


def statistics():
    return dict(rotating_coordinate_segments=0,preconditioner_direct_solves=0,reused_step_history=0,segments=0,last_accepted_time_s=0.,jvp_evaluations=0,linear_iterations=0,linear_solves=0,linearizations=0,reused_linearizations=0,peak_local_linearization_scalar_slots=0,preconditioner_factorizations=0,maximum_preconditioner_block_size=0,
                linear_retries=0,accepted_steps=0,peak_segment_retained_scalars=0,maximum_consistency_kcl_pu=0.,maximum_linear_relative_residual=0.,maximum_linear_residual_ratio=0.)
