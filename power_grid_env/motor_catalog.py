"""Traceable public reference motors and explicitly authored load fixtures.

These are reproducible numerical fixtures, not manufacturer-certified passports.
"""
import math
from power_grid_env.flux_machine import FluxMachinePassport,WindingThermal

CATALOG_IDS=('aalto-linear-fan','aalto-saturated-fan','aalto-saturated-viscous-thermal')
LINEAR_SOURCE='https://aalto-electric-drives.github.io/motulator/drive_examples/grid_connection/plot_2kw_im_dol.html'
SATURATED_SOURCE='https://aalto-electric-drives.github.io/motulator/drive_examples/current_vector/plot_2kw_im_sat_cvc.html'


def catalog_entry(identifier,*,motor_id='m',node_id='motor'):
    if identifier not in CATALOG_IDS:raise ValueError('unknown motor catalog identifier')
    linear=identifier=='aalto-linear-fan';thermal=identifier.endswith('thermal')
    # Exact constant inverse-Gamma -> Gamma referral, g=L_M/(L_M+L_sgm).
    g=.224/(.224+.021)
    p=FluxMachinePassport(id=motor_id,node_id=node_id,stator_r_ohm=3.7,
        unsaturated_h=.224+.021 if linear else .34,saturation_beta_per_vs=0. if linear else .84,
        cages=[dict(resistance_ohm=2.1/g**2 if linear else 2.5,leakage_h=.021/g if linear else .023)],
        pole_pairs=2,inertia_kg_m2=.03,fan_nm_per_rad_s2=0. if thermal else 14.6/(math.pi*50)**2,
        viscous_nm_per_rad_s=14.6/(math.pi*50) if thermal else 0.,
        provenance=dict(electrical=LINEAR_SOURCE if linear else SATURATED_SOURCE,
                        magnetics=LINEAR_SOURCE if linear else SATURATED_SOURCE,
                        mechanical='Authored J=0.03 kg m2; fan torque=14.6 Nm at 1500 rpm' if not thermal else 'Authored J=0.03 kg m2; viscous torque=14.6 Nm at 1500 rpm'))
    if thermal:
        p.thermal=WindingThermal(stator_capacity_j_per_k=100.,rotor_capacity_j_per_k=80.,stator_ambient_w_per_k=1.,rotor_ambient_w_per_k=1.,cross_w_per_k=.2)
        p.provenance['thermal']='Authored two-node numerical fixture; heat capacities and cooling are not measured'
    return dict(id=identifier,motor=p,frequency_hz=50.,nominal_ll_v=400.,
                provenance_kind='published_equivalent_circuit_with_authored_mechanism',
                applicability='balanced full-winding Gamma; DOL; no measured hardware validation',
                parameter_mapping='constant inverse-Gamma transformed exactly to Gamma' if linear else 'published saturated Gamma',
                acceptance_reference='motulator 0.19.0 original flux RHS and independent SI network',
                protection_applicability='temperature validity ceiling only; not a calibrated thermal relay')


def catalog_study(identifier,*,disturbance=True):
    from power_grid_env.build import NetBuilder
    from power_grid_env.flux_network import FluxNetworkSettings
    entry=catalog_entry(identifier)
    b=NetBuilder('catalog-'+identifier,'Public motor and authored mechanism acceptance',.4,s_base_va=5000)
    b.node('grid');b.node('motor');b.source('grid',.05,0.)
    b.line_z1('grid','motor',.12+.23j,line_id='feeder')
    events=[dict(time_s=.4,source_id='source',voltage_pu=.2),dict(time_s=.5,source_id='source',voltage_pu=1.)] if disturbance else []
    return b.build(),FluxNetworkSettings(motors=[entry['motor']],duration_s=1.,step_s=.002,events=events)


def protected_catalog_study(identifier,mode='restart'):
    """Authored relay/RC-MOV fixture with the selected public motor passport."""
    name=identifier+'__'+mode
    if identifier not in CATALOG_IDS or mode not in {'restart','locked','temperature','cool_reclose'} or (mode=='temperature' and identifier!='aalto-saturated-viscous-thermal'):raise ValueError('known catalog mechanism and supported protection mode required')
    from power_grid_env.phase_network import PhaseCase
    from power_grid_env.protected_motor import ProtectedMotorStudy
    p=catalog_entry(identifier)['motor'];p.locked_rotor=mode in {'locked','cool_reclose'}
    phases=('a','b','c');sources=[];resistors=[];capacitors=[]
    for k,(phase,angle) in enumerate(zip(phases,(0.,-2*math.pi/3,2*math.pi/3))):
        envelope=[(0.,1.),(.4,1.),(.401,.2),(.5,.2),(.501,1.)] if mode=='restart' else [(0.,1.)]
        sources.append(dict(id='source_'+phase,node='s'+phase,tones=[dict(order=1,rms=400/math.sqrt(3),phase_rad=angle)],envelope=envelope))
        resistors += [dict(id='grid_'+phase,frm='s'+phase,to='f'+phase,resistance_ohm=.17),dict(id='contact_'+phase,frm='f'+phase,to='m'+phase,resistance_ohm=.02)]
        capacitors.append(dict(id='snubber_'+phase,frm='f'+phase,to='m'+phase,capacitance_f=1e-6))
    duration=1. if mode=='restart' else 3. if mode=='cool_reclose' else .6
    reset_time=2.1 if mode=='cool_reclose' else .5
    events=[] if mode=='restart' else [dict(time_s=reset_time+.01,kind='resistance',equipment_id='contact_'+phase,value=.02) for phase in phases]
    c=PhaseCase(id='protected-'+name,nodes=[prefix+phase for prefix in ('s','f','m') for phase in phases],sources=sources,resistors=resistors,capacitors=capacitors,
        motors=[dict(id='motor',nodes=('ma','mb','mc'),passport=p)],events=events,
        provenance=dict(electrical=p.provenance['electrical'],magnetics=p.provenance['magnetics'],mechanical=p.provenance['mechanical'],
                        breaker='Authored finite contact + 1 uF snubber + passive 600 V/2 ohm clamp; not an OEM breaker',relay='Authored accelerated I2t relay, not OEM settings'))
    return ProtectedMotorStudy(case=c,contact_ids=('contact_a','contact_b','contact_c'),supply_nodes=('sa','sb','sc'),duration_s=duration,maximum_runup_s=.9 if mode=='restart' else .5,
        reset_commands=[] if mode=='restart' else [dict(time_s=reset_time)],
        relay=dict(id='motor_relay',rated_current_rms_a=5.,time_constant_s=1.,trip_level=100. if mode=='temperature' else 5.,
                   stator_trip_c=23. if mode=='temperature' else None,reset_level=1.,provenance=c.provenance['relay']))
