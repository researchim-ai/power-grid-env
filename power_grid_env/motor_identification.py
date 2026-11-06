"""Bounded weighted steady-state Gamma parameter identification, not field proof."""
import hashlib,json,math,time
import numpy as np
from scipy.optimize import least_squares
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.flux_machine import FluxMachinePassport


class SteadyMotorObservation(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    frequency_hz:float=Field(ge=.1,le=1000)
    mechanical_rad_s:float=Field(ge=-10000,le=10000)
    voltage_alpha_v:float=Field(ge=-1e6,le=1e6)
    voltage_beta_v:float=Field(ge=-1e6,le=1e6)
    current_alpha_a:float=Field(ge=-1e8,le=1e8)
    current_beta_a:float=Field(ge=-1e8,le=1e8)
    sigma_a:float=Field(default=.001,gt=0,le=10000)


class MotorIdentification(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    coordinates:str='peak_alpha_beta_steady'
    initial:FluxMachinePassport
    fitting:list[SteadyMotorObservation]=Field(min_length=6,max_length=2048)
    validation:list[SteadyMotorObservation]=Field(min_length=4,max_length=2048)
    data_provenance:str=Field(min_length=1)
    maximum_evaluations:int=Field(default=2000,ge=1,le=5000)
    maximum_wall_s:float=Field(default=60,gt=0,le=600)
    validation_tolerance_sigma:float=Field(default=5.,gt=0,le=100)
    maximum_condition:float=Field(default=1e6,gt=1,le=1e10)

    @model_validator(mode='after')
    def declared(self):
        if self.coordinates!='peak_alpha_beta_steady':raise ValueError('explicit peak SI steady coordinates required; RMS must be converted before fitting')
        if self.initial.thermal is not None:raise ValueError('identification requires known fixed winding temperatures; remove dynamic thermal feedback from this steady passport')
        if not self.data_provenance.strip():raise ValueError('nonblank data provenance required')
        if {json.dumps(x.model_dump(exclude={'sigma_a'}),sort_keys=True) for x in self.fitting}&{json.dumps(x.model_dump(exclude={'sigma_a'}),sort_keys=True) for x in self.validation}:raise ValueError('fitting and held-out rows must be distinct')
        values=[self.initial.stator_r_ohm,self.initial.unsaturated_h,*[v for c in self.initial.cages for v in [c.resistance_ohm,c.leakage_h]]]
        if any(v<1e-12 or v>1e6 for v in values):raise ValueError('identification seed outside declared representable parameter range')
        return self


def identify(spec):
    started=time.monotonic();calls=0;seed=spec.initial;names=['stator_r_ohm','unsaturated_h']
    if seed.saturation_beta_per_vs>0:names.append('saturation_beta_per_vs')
    if seed.core_loss_s>0:names.append('core_loss_s')
    names += [f'cage{i+1}_{part}' for i in range(len(seed.cages)) for part in ['r','l']]
    values=[getattr(seed,k) if not k.startswith('cage') else getattr(seed.cages[int(k.split('_')[0][4:])-1], 'resistance_ohm' if k.endswith('_r') else 'leakage_h') for k in names]
    digest=hashlib.sha256(json.dumps(dict(coordinates=spec.coordinates,fitting=[r.model_dump() for r in spec.fitting],validation=[r.model_dump() for r in spec.validation],provenance=spec.data_provenance),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def unpack(log):
        p=seed.model_copy(deep=True)
        for key,value in zip(names,np.exp(log)):
            if key.startswith('cage'):
                cage=p.cages[int(key.split('_')[0][4:])-1];setattr(cage,'resistance_ohm' if key.endswith('_r') else 'leakage_h',float(value))
            else:setattr(p,key,float(value))
        return p
    def residual(log,rows):
        nonlocal calls
        calls+=1
        if calls>spec.maximum_evaluations:raise TimeoutError('identification evaluation budget exceeded')
        if time.monotonic()-started>spec.maximum_wall_s:raise TimeoutError('identification wall budget exceeded')
        p=unpack(log);result=[]
        for row in rows:
            w=2*math.pi*row.frequency_hz;slip=w-p.pole_pairs*row.mechanical_rad_s
            v=complex(row.voltage_alpha_v,row.voltage_beta_v);current=complex(row.current_alpha_a,row.current_beta_a)
            psi=(v-p.stator_r_ohm*current)/(1j*w)
            rotor=sum(-1j*slip*psi/complex(c.resistance_ohm,slip*c.leakage_h) for c in p.cages)
            predicted=psi*(1+(p.saturation_beta_per_vs*abs(psi))**p.saturation_exponent)/p.unsaturated_h-rotor+1j*w*p.core_loss_s*psi
            error=(predicted-current)/row.sigma_a;result.extend([error.real,error.imag])
        return np.array(result)
    lower=np.log(np.array(values)/10);upper=np.log(np.array(values)*10)
    if 'core_loss_s' in names:upper[names.index('core_loss_s')]=min(upper[names.index('core_loss_s')],0.)
    try:
        fit=least_squares(lambda log:residual(log,spec.fitting),np.log(values),bounds=(lower,upper),jac='3-point',max_nfev=spec.maximum_evaluations,xtol=1e-12,ftol=1e-12,gtol=1e-12)
        fitting_errors=residual(fit.x,spec.fitting);errors=residual(fit.x,spec.validation);_,singular,vh=np.linalg.svd(fit.jac,full_matrices=False)
        rank=int(sum(singular>singular[0]*1e-10));condition=float(singular[0]/singular[-1]) if singular[-1]>0 else None
        identifiable=rank==len(names) and condition is not None and condition<=spec.maximum_condition
        within=bool(np.max(abs(errors))<=spec.validation_tolerance_sigma and np.max(abs(fitting_errors))<=spec.validation_tolerance_sigma)
        passport=unpack(fit.x);origin='identified from dataset '+digest+'; not independent field qualification'
        passport.provenance.update(electrical=origin,magnetics=origin,identification=origin)
        if passport.core_loss_s:passport.provenance['core_loss']=origin
        passport=FluxMachinePassport.model_validate(passport.model_dump())
        result=dict(completed=True,passed=bool(fit.success and identifiable and within),identifiable=identifiable,weighted_jacobian_rank=rank,parameters=len(names),
            condition=condition,maximum_condition=spec.maximum_condition,log_parameters=names,maximum_fitting_error_sigma=float(max(abs(fitting_errors))),maximum_validation_error_sigma=float(max(abs(errors))),validation_tolerance_sigma=spec.validation_tolerance_sigma,
            fitting_rows=len(spec.fitting),held_out_rows=len(spec.validation),nfev=fit.nfev,evaluations=calls,dataset_sha256=digest,data_provenance=spec.data_provenance,
            passport=passport.model_dump(mode='json'),seed_passport=seed.model_dump(mode='json'),field_validated=False,industrial_profile_qualified=False,
            method='weighted_complex_steady_Gamma_equations_log_positive_parameters_3point_Jacobian')
        return result
    except TimeoutError as exc:return dict(completed=False,passed=False,status='runtime_limit_exceeded',detail=str(exc),evaluations=min(calls,spec.maximum_evaluations),dataset_sha256=digest)
    except (ValueError,ArithmeticError) as exc:return dict(completed=False,passed=False,status='numerically_not_converged',detail=str(exc),dataset_sha256=digest)
