"""Authored diode-fed drives on explicit six-node RLC, not OEM passports."""
import copy
from benchmarks.vf_core_cases import document as active_document
from power_grid_env.phase_network import PhaseCase

NAMES=('precharge','early_bypass','sag_reconnect','blocked_regeneration','braked_regeneration','current_control','current_control_sag')


def document(name):
    if name not in NAMES:raise ValueError('unknown diode study')
    d=copy.deepcopy(active_document('braking_resistor' if name.endswith('regeneration') else 'core_runup'))
    d['id']=name;c=d['case'];c['id']='diode_'+name;p=c['drives'][0]
    p.update(diode_front_end=dict(diode_resistance_ohm=.4,series_resistance_ohm=2.,precharge_resistance_ohm=60.,diode_drop_v=.9,precharge_bypassed=False,provenance='authored resistively commutated six-pulse bridge; total DC path resistance, not per-phase resistance'),rectifier_efficiency=1.,ramp_s=.2,dc_capacitance_f=.002,initial_dc_v=0.,connected=True)
    c['events']=[dict(time_s=.015 if name=='early_bypass' else .06,kind='drive_precharge_bypass',equipment_id='drive',value=1.)]
    if name in {'sag_reconnect','current_control_sag'}:
        c['sources'][1]['envelope']=[(0.,1.),(.035,1.),(.045,.3),(.07,.3),(.08,1.)]
        c['events'] += [dict(time_s=t,kind='converter_connection',equipment_id='drive',value=v) for t,v in [(.04,0.),(.085,1.)]]
    if name.endswith('regeneration'):
        p.update(initial_dc_v=700.,ramp_s=.0002)
        p['diode_front_end']['precharge_bypassed']=True;c['events']=[]
        if name=='blocked_regeneration':
            for key in ['braking_resistance_ohm','braking_start_v','braking_full_v','braking_provenance']:p.pop(key,None)
    if name.startswith('current_control'):
        p['current_control']=dict(direct_peak_a=6.,quadrature_peak_a=1.,ramp_s=.02,kp_ohm=30.,ki_ohm_per_s=2000.,antiwindup_per_s=200.,provenance='authored prescribed synchronous-frame PI; not FOC or speed control')
    c['provenance']['rectifier']='ideal blocking law + fixed diode drops + finite DC path R; no overlap, reverse recovery or DC choke; authored switching times'
    d['case']=PhaseCase.model_validate(c).model_dump(mode='json');d['emt'].update(duration_s=.12,sample_s=.00025,max_step_s=.0001)
    return d
