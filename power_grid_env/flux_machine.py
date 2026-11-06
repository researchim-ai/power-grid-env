"""Passive saturated Gamma winding model in peak SI alpha-beta coordinates.

All stator and rotor winding dynamics are retained. Currents are coordinates;
fluxes are recovered from a strictly monotone magnetic characteristic. Multiple
rotor branches model a discrete cage spectrum, not an arbitrary skin-depth law.
"""
import math
import numpy as np
from scipy.optimize import brentq
from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer


class CageWinding(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    resistance_ohm: float = Field(gt=0)
    leakage_h: float = Field(gt=0)


class WindingThermal(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    reference_c: float = 20.
    ambient_c: float = 20.
    alpha_per_c: float = Field(default=.00393, ge=0, le=.02)
    stator_capacity_j_per_k: float = Field(gt=0)
    rotor_capacity_j_per_k: float = Field(gt=0)
    stator_ambient_w_per_k: float = Field(ge=0)
    rotor_ambient_w_per_k: float = Field(ge=0)
    cross_w_per_k: float = Field(default=0., ge=0)
    maximum_c: float = Field(default=200., gt=0, le=1000)

    @model_validator(mode='after')
    def representable(self):
        if self.ambient_c >= self.maximum_c or 1+self.alpha_per_c*(self.ambient_c-self.reference_c) <= 0:
            raise ValueError('thermal range must have positive resistances and room below maximum_c')
        return self


class FluxMachinePassport(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    stator_r_ohm: float = Field(gt=0)
    unsaturated_h: float = Field(gt=0)
    saturation_beta_per_vs: float = Field(default=0., ge=0)
    saturation_exponent: int = Field(default=7, ge=1, le=20)
    core_loss_s: float = Field(default=0., ge=0, le=1.)
    cages: list[CageWinding] = Field(min_length=1, max_length=4)
    pole_pairs: int = Field(ge=1, le=20)
    inertia_kg_m2: float = Field(gt=0)
    fan_nm_per_rad_s2: float = Field(default=0., ge=0)
    viscous_nm_per_rad_s: float = Field(default=0., ge=0)
    initial_speed_rad_s: float = Field(default=0., ge=-2000, le=2000)
    locked_rotor: bool = False
    thermal: WindingThermal | None = None
    provenance: dict[str, str]

    @model_serializer(mode='wrap')
    def preserve_lossless_passport(self,handler):
        result=handler(self)
        if self.core_loss_s==0:result.pop('core_loss_s',None)
        return result

    @model_validator(mode='after')
    def sourced(self):
        required={'electrical','magnetics','mechanical'} | ({'thermal'} if self.thermal else set()) | ({'core_loss'} if self.core_loss_s else set())
        if not required <= self.provenance.keys() or any(not str(v).strip() for v in self.provenance.values()):
            raise ValueError('electrical, magnetics, mechanical and enabled thermal parameter sources required')
        if self.locked_rotor and self.initial_speed_rad_s != 0:
            raise ValueError('locked rotor requires zero initial speed')
        return self


class GammaWinding:
    def __init__(self, passport):
        self.p=passport
        self.leakage=np.array([c.leakage_h for c in passport.cages])
        self.rotor_r=np.array([c.resistance_ohm for c in passport.cages])

    def magnetic(self, currents):
        """Return stator flux, rotor fluxes, differential reluctance and energy."""
        p=self.p;currents=np.asarray(currents);im=sum(currents);amplitude=abs(im)
        if p.saturation_beta_per_vs == 0 or amplitude == 0:
            rho=p.unsaturated_h*amplitude
        else:
            beta=p.saturation_beta_per_vs;exponent=p.saturation_exponent
            rho=brentq(lambda q:q*(1+(beta*q)**exponent)-p.unsaturated_h*amplitude,
                       0.,p.unsaturated_h*amplitude,xtol=1e-14,rtol=1e-14)
        psi=im*(rho/amplitude) if amplitude else 0j
        saturation=(p.saturation_beta_per_vs*rho)**p.saturation_exponent
        reluctance=np.eye(2)*(1+saturation)/p.unsaturated_h
        if rho:
            direction=np.array([psi.real,psi.imag])/rho
            reluctance+=p.saturation_exponent*saturation/p.unsaturated_h*np.outer(direction,direction)
        rotor=psi+self.leakage*currents[1:]
        energy=1.5/p.unsaturated_h*(rho*rho/2+saturation*rho*rho/(p.saturation_exponent+2))
        energy+=.75*float(np.sum(self.leakage*abs(currents[1:])**2))
        return psi,rotor,reluctance,float(energy)

    def quantities(self,currents,speed,temperature):
        if self.p.core_loss_s:raise ValueError('positive Gc requires flux-state network equations, not the lossless current-coordinate winding')
        p=self.p;psi,rotor,reluctance,energy=self.magnetic(currents)
        if p.thermal:
            multipliers=1+p.thermal.alpha_per_c*(np.asarray(temperature)-p.thermal.reference_c)
            if np.any(multipliers<=0) or np.max(temperature)>p.thermal.maximum_c:
                raise ValueError('winding temperature outside the declared resistance range')
            rs=p.stator_r_ohm*multipliers[0];rr=self.rotor_r*multipliers[1]
        else:rs=p.stator_r_ohm;rr=self.rotor_r
        torque=float(1.5*p.pole_pairs*(currents[0]*np.conj(psi)).imag)
        copper_s=float(1.5*rs*abs(currents[0])**2)
        copper_r=float(1.5*np.sum(rr*abs(currents[1:])**2))
        Y=reluctance+np.eye(2)*sum(1/self.leakage)
        dr=-rr*currents[1:]+1j*p.pole_pairs*speed*rotor
        offset=-Y@(rs*np.array([currents[0].real,currents[0].imag]))
        term=-sum(dr/self.leakage);offset+=np.array([term.real,term.imag])
        return dict(psi=psi,rotor_flux=rotor,reluctance=reluctance,energy_j=energy,
                    rs=rs,rr=rr,torque_nm=torque,stator_copper_w=copper_s,rotor_copper_w=copper_r,
                    Y=Y,offset=offset,rotor_flux_derivative=dr)

    def derivatives(self,currents,speed,temperature,voltage,q=None):
        p=self.p;q=self.quantities(currents,speed,temperature) if q is None else q
        ds=voltage-q['rs']*currents[0]
        di_s=q['Y']@np.array([voltage.real,voltage.imag])+q['offset']
        di_r=(q['rotor_flux_derivative']-ds)/self.leakage
        mechanical=0. if p.locked_rotor else (q['torque_nm']-p.fan_nm_per_rad_s2*speed*abs(speed)-p.viscous_nm_per_rad_s*speed)/p.inertia_kg_m2
        thermal=np.zeros(2);ambient_loss=0.
        if p.thermal:
            h=p.thermal;ts,tr=temperature;cross=h.cross_w_per_k*(ts-tr)
            hs=h.stator_ambient_w_per_k*(ts-h.ambient_c);hr=h.rotor_ambient_w_per_k*(tr-h.ambient_c)
            thermal=np.array([(q['stator_copper_w']-hs-cross)/h.stator_capacity_j_per_k,
                              (q['rotor_copper_w']-hr+cross)/h.rotor_capacity_j_per_k]);ambient_loss=hs+hr
        return np.r_[complex(*di_s),di_r],mechanical,thermal,float(ambient_loss)
