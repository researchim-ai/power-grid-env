"""Authored switching-drive corpus: no manufacturer or field accuracy claim."""
from pathlib import Path
from power_grid_env.study_artifacts import read_json
from power_grid_env.switching_drive import SwitchingDriveStudy
ROOT=Path(__file__).resolve().parents[1]
NAMES=('foc_start','pwm_block_recover','grid_sag','inverter_thermal_trip','rectifier_thermal_trip','brake_thermal_trip','regeneration')


def document(name):
    if name not in NAMES:raise ValueError('unknown switching-drive case')
    m=read_json(ROOT/'cases/studies/flux-saturated_dol.json')['flux_network']['motors'][0]
    m.update(saturation_beta_per_vs=0.,core_loss_s=0.,thermal=None,initial_speed_rad_s=0.,fan_nm_per_rad_s2=.0001)
    p=dict(id=name,motor=m,control=dict(flux_reference_vs=.6,flux_ramp_s=.025,speed_reference_rad_s=30.,speed_ramp_s=.1,current_limit_peak_a=10.,torque_enable_flux_fraction=.25,speed_kp_nm_per_rad_s=.8,speed_ki_nm_per_rad=2.,current_kp_ohm=30.,current_ki_ohm_per_s=3000.,antiwindup_per_s=100.,provenance='authored sensored rotor-flux FOC/PI tuning, not OEM'),thermal=dict(capacity_j_per_k=[40.,30.,50.],conductance_w_per_k=[2.,2.,1.],trip_c=[150.,150.,150.],provenance='authored three thermal lumps, not a module impedance fit'),duration_s=.16,max_step_s=1e-5,sample_s=.0002,pwm_frequency_hz=2500.,dead_time_s=2e-6,grid_ln_rms_v=230.,source_inductance_h=.003,source_resistance_ohm=.3,precharge_resistance_per_phase_ohm=20.,precharge_ready_v=450.,precharge_min_s=.002,dc_capacitance_f=.002,rail_earth_capacitance_f=2e-6,initial_dc_v=540.,bleed_ohm=2000.,rectifier_drop_v=.9,rectifier_resistance_ohm=.15,mosfet_resistance_ohm=.2,body_diode_drop_v=.9,body_diode_resistance_ohm=.15,switching_on_j=.001,switching_off_j=.0007,switching_reference_v=600.,switching_reference_a=10.,switching_temperature_per_k=.002,enable_dc_v=460.,disable_dc_v=380.,brake_resistance_ohm=40.,brake_start_v=650.,brake_full_v=700.,provenance=dict(network='authored 230V LN / 3mH source RL and explicit 2uF rail-earth C',semiconductors='authored linear on-R, diode Vf/R; ideal blocking; not charge-dynamic devices',switching='authored reference Eon/Eoff linear |I|/Iref*Vdc/Vref and temperature factor',thermal='authored RC heat/ambient paths; three latching thresholds',control='current-model rotor-flux observer uses current and shaft speed; inverse-Gamma published equations'))
    if name=='foc_start':p['duration_s']=.48
    if name=='pwm_block_recover':p['gate_block_windows']=[(.03,.04)]
    if name=='grid_sag':p['grid_envelope']=[(0.,1.),(.025,1.),(.03,.5),(.045,.5),(.05,1.)]
    if name=='inverter_thermal_trip':p['thermal'].update(initial_c=[25.,29.9,25.],capacity_j_per_k=[40.,.03,50.],conductance_w_per_k=[2.,.01,1.],trip_c=[150.,30.,150.])
    if name=='rectifier_thermal_trip':p['thermal'].update(initial_c=[25.,25.,25.],capacity_j_per_k=[40.,30.,50.],conductance_w_per_k=[.01,2.,1.],trip_c=[25.2,150.,150.]);p['initial_dc_v']=300.
    if name in {'regeneration','brake_thermal_trip'}:
        p['motor']['initial_speed_rad_s']=180.;p['motor']['fan_nm_per_rad_s2']=0.;p['control']['speed_reference_rad_s']=-30.;p['control']['speed_ramp_s']=.01;p['control']['flux_ramp_s']=.01;p['brake_start_v']=560.;p['brake_full_v']=600.
    if name=='brake_thermal_trip':p['thermal'].update(capacity_j_per_k=[40.,30.,.02],conductance_w_per_k=[2.,2.,.01],trip_c=[150.,150.,25.1])
    return SwitchingDriveStudy.model_validate(p).model_dump(mode='json')
