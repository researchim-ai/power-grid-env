"""Operating-point eigenanalysis of supported canonical dynamic models.

Participation is recomputed from biorthogonal vectors and normalized per mode.
Repeated/ill-conditioned modes do not receive invented state attributions.
"""
from __future__ import annotations
import math
import numpy as np
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.rms_study import initialize_dynamics


def analyze_matrix(matrix, names, *, tolerance=1e-8):
    if not math.isfinite(tolerance) or tolerance<=0:
        raise ValueError('positive finite stability tolerance required')
    raw=np.asarray(matrix)
    if np.iscomplexobj(raw) and np.any(raw.imag!=0):
        raise ValueError('real state matrix required')
    a=np.asarray(raw.real,dtype=float)
    if a.shape!=(len(names),len(names)) or not len(names) or not np.isfinite(a).all():
        raise ValueError('finite nonempty square state matrix and matching labels required')
    values,right=np.linalg.eig(a)
    order=sorted(range(len(values)),key=lambda i:(float(values[i].real),float(values[i].imag)))
    values=values[order];right=right[:,order]
    if not np.isfinite(values).all() or not np.isfinite(right).all():
        raise ValueError('nonfinite eigenpairs')
    residual=float(np.linalg.norm(a@right-right*values,ord=np.inf)/max(1.,np.linalg.norm(a,ord=np.inf)*np.linalg.norm(right,ord=np.inf)))
    if residual>1e-8:raise ValueError('eigenpair residual exceeds tolerance')
    condition=float(np.linalg.cond(right))
    repeated=any(abs(values[i]-values[j])<=1e-7*max(1.,abs(values[i]),abs(values[j]))
                 for i in range(len(values)) for j in range(i))
    participation=None
    if not math.isfinite(condition) or condition>1e10:
        participation_status='inconclusive_ill_conditioned'
    elif repeated:
        participation_status='inconclusive_repeated_modes'
    else:
        left=np.linalg.inv(right)
        raw=np.abs(right*left.T)
        participation=raw/raw.sum(axis=0,keepdims=True)
        participation_status='computed_biorthogonal_magnitude_normalized_per_mode'
    modes=[]
    for i,value in enumerate(values):
        magnitude=abs(value)
        state_factors=None if participation is None else [
            {'state':str(names[j]),'factor':float(participation[j,i])}
            for j in np.argsort(-participation[:,i])]
        modes.append({'id':f'mode_{i:03d}','real_per_s':float(value.real),'imag_rad_per_s':float(value.imag),
                      'frequency_hz':abs(float(value.imag))/(2*math.pi),
                      'damping_ratio':None if magnitude<=tolerance else -float(value.real)/float(magnitude),
                      'participation':state_factors})
    abscissa=float(max(values.real))
    stability='unstable' if abscissa>tolerance else 'marginal_or_numerically_near_axis' if abscissa>=-tolerance else 'asymptotically_stable_linearized'
    return {'modes':modes,'spectral_abscissa_per_s':abscissa,'linear_stability':stability,
            'eigenpair_residual':residual,'eigenvector_condition_number':condition if math.isfinite(condition) else None,
            'participation_status':participation_status,'stability_tolerance_per_s':tolerance}


def _canonical_state_names(system,ids):
    lookup={}
    groups=[(ids['machine_models'][gen],{gen:token}) for gen,token in ids['machines'].items()]
    groups.extend([(group,ids[key]) for group,key in [('TGOV1','governors'),('SEXS','exciters'),('IEEEST','stabilizers')]])
    for group,mapping in groups:
        model=getattr(system,group)
        for gen,token in mapping.items():
            index=list(model.idx.v).index(token)
            for variable_name,variable in model.states.items():
                address=int(np.asarray(variable.a).ravel()[index])
                prefix=gen if group in {'GENCLS','GENROU'} else f'{gen}.{group}'
                lookup[str(system.dae.x_name[address])]=f'{prefix}.{variable_name}'
    return [lookup.get(str(name),'backend:'+str(name)) for name in system.EIG.x_name]


def _limit_boundaries(system,ids,settings,base):
    boundaries=[]
    for machine in settings.machines:
        gen=machine.generator_id
        checks=[]
        if machine.governor:
            model=system.TGOV1;i=list(model.idx.v).index(ids['governors'][gen]);factor=machine.rated_va/base
            checks.append(('governor_valve',float(model.LAG_y.v[i]),machine.governor.VMIN*factor,machine.governor.VMAX*factor))
        if machine.exciter:
            model=system.SEXS;i=list(model.idx.v).index(ids['exciters'][gen])
            checks.append(('exciter_field',float(model.vout.v[i]),machine.exciter.EMIN,machine.exciter.EMAX))
        if machine.stabilizer:
            model=system.IEEEST;i=list(model.idx.v).index(ids['stabilizers'][gen])
            checks.extend([('pss_output',float(model.vsout.v[i]),machine.stabilizer.LSMIN,machine.stabilizer.LSMAX),
                           ('pss_voltage_gate',float(model.v.v[i]),machine.stabilizer.VCL,machine.stabilizer.VCU)])
        for name,value,low,high in checks:
            if value<=low+1e-7 or value>=high-1e-7:
                boundaries.append(f'{gen}.{name}')
    return boundaries


def run_small_signal(network,settings):
    if settings.events or settings.faults:
        return {'solved':False,'completed':False,'status':'rejected','reason':'small_signal_operating_point_only'}
    # This version deliberately uses dense modal decomposition on a bounded
    # dynamic state count. It does not claim arbitrary network-scale eigenanalysis.
    if len(settings.machines)>8:
        return {'solved':False,'completed':False,'status':'rejected','reason':'small_signal_machine_budget'}
    initialized,error=initialize_dynamics(network,settings)
    if error is not None:return error
    system,ids,scope,initial,residual=initialized
    if system.dae.n>256:
        return {'solved':False,'completed':False,'status':'rejected','reason':'small_signal_state_budget'}
    try:
        system.j_update(system.exist.pflow_tds)
        matrix=np.asarray(system.EIG.calc_As(),dtype=float)
        names=_canonical_state_names(system,ids)
        analyzed=analyze_matrix(matrix,names)
    except (ValueError,ArithmeticError,np.linalg.LinAlgError) as exc:
        return {'solved':True,'completed':False,'status':'linearization_failed','error':str(exc),'init_residual':residual}
    boundaries=_limit_boundaries(system,ids,settings,network.s_base_va)
    if boundaries:
        analyzed['linear_stability']='inconclusive_at_controller_boundary'
    import andes
    return {**analyzed,'solved':True,'completed':True,'status':'completed',
            'time_domain_simulated':False,'backend':'andes','backend_version':andes.__version__,'study':'small_signal',
            'scope':scope,'initial':initial,'init_residual':residual,'mapping':ids,
            'state_names':names,'state_matrix_per_s':matrix.tolist(),'controller_boundary_flags':boundaries,
            'linearization':'T^-1 (fx - fy gy^-1 gx); algebraic state elimination',
            'participation_note':'Normalized modal state attribution, not energy or transient stability proof',
            'code_sha256':package_fingerprint(),'network_sha256':canonical_hash(network),
            'settings':settings.model_dump(mode='json'),'industrial_profile_qualified':False,'field_validated':False}
