"""Authored finite V/f drive studies; parameters are not manufacturer-qualified."""
import copy
from pathlib import Path
import numpy as np
from power_grid_env.phase_network import PhaseCase
from power_grid_env.study_artifacts import read_json
ROOT=Path(__file__).resolve().parents[1]
NAMES=('core_runup','thermal_multicage','sag_block_recover','regeneration','dc_undervoltage','braking_resistor')


def document(name):
    if name not in NAMES:raise ValueError('unknown registered drive case')
    p=read_json(ROOT/'cases/studies/flux-saturated_dol.json')['flux_network']['motors'][0];p['id']='machine';p['core_loss_s']=.006;p['provenance']['core_loss']='authored 0.006 S, published Gamma eddy-current equation; not a measured loss map'
    if name=='thermal_multicage':
        p['cages'] += [dict(resistance_ohm=5.+i,leakage_h=.018+.004*i) for i in range(3)]
        p['thermal']=dict(stator_capacity_j_per_k=2000.,rotor_capacity_j_per_k=1000.,stator_ambient_w_per_k=10.,rotor_ambient_w_per_k=8.,cross_w_per_k=2.);p['provenance']['thermal']='authored two-lump cooling/capacities'
    if name=='sag_block_recover':p['cages'].append(dict(resistance_ohm=5.,leakage_h=.018))
    if name in {'regeneration','braking_resistor'}:p.update(initial_speed_rad_s=350.,fan_nm_per_rad_s2=0.,viscous_nm_per_rad_s=0.)
    drive=dict(id='drive',nodes=['a','b','c'],motor=p,grid_ln_rms_v=230.,grid_current_limit_rms_a=3. if name=='thermal_multicage' else 8. if name=='sag_block_recover' else 30.,dc_capacitance_f=.002 if name=='dc_undervoltage' else .02,dc_reference_v=700.,initial_dc_v=180. if name=='dc_undervoltage' else 700.,motor_ln_rms_v=230.,ramp_s=.015 if name=='dc_undervoltage' else .01 if name in {'regeneration','braking_resistor'} else .04,rectifier_efficiency=.97,inverter_efficiency=.96,bleed_ohm=1000. if name=='dc_undervoltage' else 2000.,maximum_modulation=.8 if name=='dc_undervoltage' else 1.,connected=name!='dc_undervoltage')
    if name in {'regeneration','braking_resistor'}:drive['motor_initial_fluxes_ab_vs']=[[.8,0.],[.75,.05]]
    if name=='braking_resistor':
        p.update(initial_speed_rad_s=180.,saturation_beta_per_vs=0.)
        omega=2*np.pi*50;slip=1-p['pole_pairs']*p['initial_speed_rad_s']/omega;cage=p['cages'][0];y=.006+1/(1j*omega*p['unsaturated_h'])+1/(cage['resistance_ohm']/slip+1j*omega*cage['leakage_h']);voltage=np.sqrt(2)*230;current=voltage*y/(1+p['stator_r_ohm']*y);psi=(voltage-p['stator_r_ohm']*current)/(1j*omega);ir=-1j*(omega-p['pole_pairs']*p['initial_speed_rad_s'])*psi/(cage['resistance_ohm']+1j*(omega-p['pole_pairs']*p['initial_speed_rad_s'])*cage['leakage_h'])
        drive['motor_initial_fluxes_ab_vs']=[[float(z.real),float(z.imag)] for z in [psi,psi+cage['leakage_h']*ir]]
        drive.update(connected=False,ramp_s=.0002,dc_capacitance_f=.001,braking_resistance_ohm=40.,braking_start_v=720.,braking_full_v=760.,braking_provenance='authored linear averaged duty above DC threshold; ideal resistor heat, no semiconductor switching')
    sources=[dict(id='grid'+n,node='s'+n,tones=[dict(order=1,rms=230.,phase_rad=angle)],envelope=[(0.,1.)]) for n,angle in zip('abc',[0.,-2*np.pi/3,2*np.pi/3])]
    if name=='sag_block_recover':sources[1]['envelope']=[(0.,1.),(.04,1.),(.05,.4),(.08,.4),(.09,1.)]
    case=dict(id=name,nodes=['sa','sb','sc','a','b','c'],sources=sources,resistors=[dict(id='load'+n,frm=n,resistance_ohm=2000.) for n in 'abc'],capacitors=[dict(id='C'+n,frm=n,capacitance_f=c) for n,c in zip('abc',[100e-6,120e-6,110e-6])],inductors=[dict(id='line'+n,frm='s'+n,to=n,resistance_ohm=r) for n,r in zip('abc',[.2,.3,.25])],inductance_h=[[.001,.0001,.00005],[.0001,.0012,.00008],[.00005,.00008,.0011]],drives=[drive],provenance=dict(origin='authored passive three-phase RLC and active averaged drive; published motor electrical/magnetic seed, declared extra cages/core/heat/control data'))
    if name=='sag_block_recover':case['events']=[dict(time_s=t,kind='converter_connection',equipment_id='drive',value=value) for t,value in [(.06,0.),(.1,1.)]]
    return dict(id=name,mode='emt',case=PhaseCase.model_validate(case).model_dump(mode='json'),emt=dict(duration_s=.2 if name in {'core_runup','thermal_multicage','sag_block_recover'} else .12,sample_s=.0005,max_step_s=.0002,rtol=1e-8,atol=1e-10))
