# SPDX-License-Identifier: MPL-2.0
# Stationary forms of Dynawo Oel2c equations, copyright (c) 2021-2024 RTE.
# See assets/licenses/Dynawo-MPL-2.0.txt.
"""Explicit active-set selection; neutral memory is an input, never a guess."""
import numpy as np


def _candidate(p,input_pu):
    measured=float(p.KScale*input_pu);actual=float(p.KAct*measured)
    if not np.isfinite([measured,actual]).all() or measured<0:raise ValueError('nonnegative finite OEL current required')
    cold=measured<=p.ITfPu
    inverse=float(np.clip(p.K2*((measured/p.ITfPu)**p.C2-1),p.VInvMinPu,p.VInvMaxPu))
    target=inverse+(p.FixedRu if cold else p.FixedRd)
    timer=float(np.clip(target/p.KFb,p.tMin,p.tMax))
    high=(p.tFcl-timer)>=p.Kzru*p.tFcl;low=timer>=p.tFcl
    ramp=p.K1*((measured/p.ITfPu)**p.C1-1) if p.Sw1 else p.Kru if high else p.Krd
    if not high and not low:ramp=0.
    supplied=p.equilibrium_reference_current_pu
    if ramp==0:
        if supplied is None:raise ValueError('ambiguous zero-ramp reference memory: equilibrium_reference_current_pu required')
        reference=supplied;selection='explicit_zero_ramp_memory'
    else:
        reference=p.IInstPu if ramp>0 else p.ILimPu;selection='outward_upper_cap' if ramp>0 else 'outward_lower_cap'
        if supplied is not None and supplied!=reference:raise ValueError('supplied reference memory contradicts outward active set')
    if not p.ILimPu<=reference<=p.IInstPu:raise ValueError('reference memory outside declared caps')
    band=p.current_comparison_tolerance_pu if 'equilibrium_guard_v2' in p.model else 0.
    above=actual>reference+band;below=reference>actual+p.IThOffPu+band
    bias=p.IResetPu if not low and not above and below and reference>=p.IInstPu-p.cap_tolerance_pu and p.tEn!=0 else 0.
    error=reference-actual+bias;memory=p.equilibrium_pid_output_pu
    if abs(error)<=1e-10:
        if memory is None:raise ValueError('ambiguous zero-error PID memory: equilibrium_pid_output_pu required')
        pid=memory;integral=pid-p.KpOel*error;pid_selection='explicit_zero_error_memory'
    else:
        pid=0. if error>0 else p.VOel3MinPu;integral=pid+(.9-p.KpOel)*error;pid_selection='upper_antiwindup' if error>0 else 'lower_antiwindup'
        if memory is not None and memory!=pid:raise ValueError('supplied PID memory contradicts antiwindup active set')
    if not p.VOel3MinPu<=pid<=0:raise ValueError('PID memory outside declared output bounds')
    lead=float(np.clip(pid,p.VOel2MinPu,0.));output=float(np.clip(lead,p.VOel1MinPu,0.))
    state=np.array([measured,timer,reference,reference,integral,p.KdOel/p.tDOel*error,
        (1-p.tB2Oel/p.tC2Oel)*lead,(1-p.tB1Oel/p.tC1Oel)*output])
    context=dict(scaled_low=cold,thermal_high=high,thermal_low=low,above=above,below=below,upper_cap=reference>=p.IInstPu-p.cap_tolerance_pu,
        timer_bound=-1 if timer==p.tMin else 1 if timer==p.tMax else 0,
        reference_bound=-1 if reference==p.ILimPu else 1 if reference==p.IInstPu else 0,
        enable_start=-(p.tEn+1.) if above else None,enable_expired=above,
        reset_start=-(p.tOff+1.) if below else None,reset_expired=below)
    audit=dict(reference_selection=selection,pid_selection=pid_selection,thermal_branch='cold' if cold else 'hot',
        reference_ramp_pu_s=float(ramp),timer_free_rate=float(target-p.KFb*timer),regulator_error_pu=float(error),
        reference_current_pu=float(reference),pid_output_pu=float(pid),output_pu=output,
        neutral_reference_memory=ramp==0,neutral_PID_memory=abs(error)<=1e-10,
        timer_active_set=context['timer_bound'],reference_active_set=context['reference_bound'],
        stability_policy='stationarity_only',full_OEL_stability_qualified=False)
    return state,context,audit


def select_stationary(p,input_pu):
    state,context,audit=_candidate(p,input_pu)
    if p.equilibrium_boundary_policy=='reject_sensitive_neighborhood':
        from power_grid_env.oel2c_boundary import check_neighborhood,SensitiveEquilibrium
        report=check_neighborhood(p,input_pu,_candidate)
        if not report['passed']:raise SensitiveEquilibrium(report)
        audit['boundary_neighborhood']=report
    return state,context,audit
