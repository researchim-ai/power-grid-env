"""Explicit single-cage T/RMS <-> linear Gamma passport referral.

The electrical transformation is a constant rotor-coordinate change. It does
not equate RMS and full-winding transients or initialize a running network.
Reverse referral needs a declared stator-leakage split; Gamma alone does not
identify the physical T-circuit split.
"""
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.induction import InductionMotor
from power_grid_env.flux_machine import FluxMachinePassport
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint

Omission=Literal['thermal_replica','contactor','disconnected_state','saturation','core_loss','winding_thermal']


class MotorTransferPlan(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    schema_version:Literal['power-grid-env.motor-transfer/v1']='power-grid-env.motor-transfer/v1'
    direction:Literal['rms_to_flux','flux_to_rms','flux_to_phase']
    frequency_hz:float=Field(gt=0,le=1000)
    pole_pairs:int=Field(ge=1,le=20)
    parameter_provenance:str=Field(min_length=1,max_length=4096)
    rms:InductionMotor | None=None
    flux:FluxMachinePassport | None=None
    rated_va:float | None=Field(default=None,gt=0)
    rated_ll_v:float | None=Field(default=None,gt=0)
    stator_leakage_fraction:float | None=Field(default=None,gt=0,lt=1)
    phase_nodes:tuple[str,str,str] | None=None
    acknowledged_omissions:list[Omission]=Field(default_factory=list,max_length=6)
    initialization:Literal['cold_zero']='cold_zero'

    @model_validator(mode='after')
    def explicit_direction(self):
        if not self.parameter_provenance.strip():raise ValueError('explicit parameter provenance required')
        if len(set(self.acknowledged_omissions))!=len(self.acknowledged_omissions):raise ValueError('duplicate omission acknowledgement')
        if self.direction=='rms_to_flux':
            if self.rms is None or self.flux is not None or any(v is not None for v in (self.rated_va,self.rated_ll_v,self.stator_leakage_fraction)):
                raise ValueError('forward transfer requires only the RMS passport and its own bases')
            if self.rms.model!='single_cage':raise ValueError('single-cage T/Gamma transfer only; coupled rotor leakage cannot be silently reduced')
            if self.rms.mechanical_load and self.rms.mechanical_load.friction_torque_pu:
                raise ValueError('Gamma target cannot represent smooth Coulomb friction')
        elif self.direction=='flux_to_rms':
            if self.flux is None or self.rms is not None or any(v is None for v in (self.rated_va,self.rated_ll_v,self.stator_leakage_fraction)):
                raise ValueError('reverse transfer requires Gamma, rated bases and explicit stator leakage fraction')
            if len(self.flux.cages)!=1:raise ValueError('one Gamma cage required; no silent cage aggregation')
            if self.pole_pairs!=self.flux.pole_pairs:raise ValueError('pole-pair mismatch')
            if self.flux.initial_speed_rad_s!=0 or self.flux.locked_rotor:
                raise ValueError('cold freely rotating passport required; warm or locked state is not transferable')
        else:
            if self.flux is None or self.rms is not None or self.phase_nodes is None or any(v is not None for v in (self.rated_va,self.rated_ll_v,self.stator_leakage_fraction)):
                raise ValueError('phase-port transfer requires Gamma and ordered explicit a/b/c nodes only')
            if self.pole_pairs!=self.flux.pole_pairs or self.flux.initial_speed_rad_s!=0:
                raise ValueError('phase port requires matching poles and cold zero speed')
        if self.direction!='flux_to_phase' and self.phase_nodes is not None:raise ValueError('phase nodes only apply to phase-port transfer')
        return self


def transfer_passport(document):
    plan=MotorTransferPlan.model_validate(document)
    wb=2*math.pi*plan.frequency_hz;wm=wb/plan.pole_pairs
    lost=[]
    if plan.direction=='flux_to_phase':
        from power_grid_env.phase_network import MotorPort
        p=plan.flux.model_copy(deep=True)
        lost=['core_loss'] if p.core_loss_s else []
        if set(lost)!=set(plan.acknowledged_omissions):raise ValueError('exact omission acknowledgement required: '+','.join(lost))
        p.core_loss_s=0.
        port=MotorPort(id=p.id,nodes=plan.phase_nodes,passport=p)
        return dict(schema='power-grid-env.motor-transfer-result/v1',producer_sha256=package_fingerprint(),direction=plan.direction,
            source=plan.flux.model_dump(mode='json'),target_kind='phase_port',target=port.model_dump(mode='json'),
            bases=dict(omega_e_rad_s=wb,omega_sync_mechanical_rad_s=wm),omitted_properties=lost,
            parameter_provenance=plan.parameter_provenance,
            initial_conditions=dict(mode='cold_zero',speed_rad_s=0.,winding_currents_a=0.,winding_fluxes_vs=0.,
                operating_point_transferred=False,network_consistency_checked=False),
            preserved=['Gamma winding passport including saturation, cage spectrum, inertia and winding thermal model',
                'explicit ordered a/b/c motor terminal mapping'],
            semantics=['direct phase motor rejects Gc; any omitted Gc is explicitly acknowledged',
                'source frequency, phase sequence and topology require separate network validation',
                'a motor port is not a solved phase-network initial condition'],
            transient_equivalence_claimed=False,network_transferred=False,field_validated=False)
    if plan.direction=='rms_to_flux':
        m=plan.rms;s=m.rated_va;v=m.rated_ll_v;z=v*v/s
        ls=m.stator_x_pu*z/wb;lm=m.magnetizing_x_pu*z/wb;lr=m.rotor_x_pu*z/wb
        lg=ls+lm;k=lg/lm
        leakage=k*k*(lm+lr)-lg;rr=k*k*m.rotor_r_pu*z
        if m.thermal:lost.append('thermal_replica')
        if m.contactor:lost.append('contactor')
        if not m.connected:lost.append('disconnected_state')
        provenance={key:plan.parameter_provenance for key in ('electrical','magnetics','mechanical')}
        fan=m.fan_torque_pu if m.mechanical_load is None else m.mechanical_load.quadratic_torque_pu
        visc=0. if m.mechanical_load is None else m.mechanical_load.viscous_torque_pu
        target=FluxMachinePassport(id=m.id,node_id=m.node_id,stator_r_ohm=m.stator_r_pu*z,
            unsaturated_h=lg,cages=[dict(resistance_ohm=rr,leakage_h=leakage)],pole_pairs=plan.pole_pairs,
            inertia_kg_m2=2*m.inertia_s*s/wm**2,fan_nm_per_rad_s2=fan*s/wm**3,
            viscous_nm_per_rad_s=visc*s/wm**2,provenance=provenance)
        source=m.model_dump(mode='json');target_kind='flux';split=ls/lg
    else:
        p=plan.flux;s=plan.rated_va;v=plan.rated_ll_v;z=v*v/s
        lg=p.unsaturated_h;ls=plan.stator_leakage_fraction*lg;lm=lg-ls;k=lg/lm
        lr=(p.cages[0].leakage_h+lg)/(k*k)-lm
        if lr<=0:raise ValueError('stator leakage split gives nonpositive T rotor leakage; choose an admissible explicit split')
        if p.saturation_beta_per_vs:lost.append('saturation')
        if p.core_loss_s:lost.append('core_loss')
        if p.thermal:lost.append('winding_thermal')
        target=InductionMotor(id=p.id,node_id=p.node_id,rated_va=s,rated_ll_v=v,stator_r_pu=p.stator_r_ohm/z,
            stator_x_pu=ls*wb/z,rotor_r_pu=p.cages[0].resistance_ohm/(k*k*z),rotor_x_pu=lr*wb/z,
            magnetizing_x_pu=lm*wb/z,inertia_s=p.inertia_kg_m2*wm**2/(2*s),
            mechanical_load=dict(viscous_torque_pu=p.viscous_nm_per_rad_s*wm**2/s,
                quadratic_torque_pu=p.fan_nm_per_rad_s2*wm**3/s))
        source=p.model_dump(mode='json');target_kind='rms';split=plan.stator_leakage_fraction
    if set(lost)!=set(plan.acknowledged_omissions):
        raise ValueError('exact omission acknowledgement required: '+','.join(sorted(lost)))
    derived=dict(omega_e_rad_s=wb,omega_sync_mechanical_rad_s=wm,z_base_ohm=z,
        torque_base_nm=s/wm,rotor_coordinate_ratio=k,stator_leakage_fraction=split)
    if not all(math.isfinite(x) and x>0 for x in derived.values()):raise ValueError('nonrepresentable physical bases or referral')
    return dict(schema='power-grid-env.motor-transfer-result/v1',producer_sha256=package_fingerprint(),direction=plan.direction,
        source=source,target_kind=target_kind,target=target.model_dump(mode='json'),bases=derived,
        omitted_properties=sorted(lost),parameter_provenance=plan.parameter_provenance,
        initial_conditions=dict(mode='cold_zero',speed_rad_s=0.,winding_currents_a=0.,winding_fluxes_vs=0.,
            operating_point_transferred=False,network_consistency_checked=False),
        preserved=['steady-state terminal impedance of the linear core-loss-free projection at every slip','shaft inertia','viscous and quadratic load law'],
        semantics=['RMS removes stator transients; full-winding retains them',
            'reverse split is a declared representation choice, not identified winding geometry',
            'saturation omission uses unsaturated inductance, not an operating-point linearization',
            'passport only; network, switching, source states and relay settings are not transferred'],
        transient_equivalence_claimed=False,network_transferred=False,field_validated=False)


def run_transfer(document,out):
    plan=MotorTransferPlan.model_validate(document);result=transfer_passport(plan)
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    (out/'input.json').write_bytes(encoded(plan.model_dump(mode='json')))
    (out/'result.json').write_bytes(encoded(result));seal(out)
    return result


def verify_transfer(root):
    root=Path(root);identity=verify(root);result=transfer_passport(read_json(root/'input.json'))
    if result!=read_json(root/'result.json'):raise ValueError('transfer differs from recomputed passport referral')
    return dict(verified=True,manifest_sha256=identity,**result)
