"""Authored electrothermal drive qualification; RC/relay values are not OEM fits."""
import copy
from benchmarks.switching_extended_cases import document as extended
from power_grid_env.switching_drive import SwitchingDriveStudy
NAMES=('cold_winding','warm_winding','saturated_four_thermal','stator_trip','rotor_trip','initial_trip_cooling','winding_cooling')


def document(name):
    if name not in NAMES:raise ValueError('unknown winding thermal case')
    d=copy.deepcopy(extended('saturated_four_cage' if name=='saturated_four_thermal' else 'saturated_core' if name in {'stator_trip','rotor_trip'} else 'core_single'))
    d['id']=name;d['duration_s']=.12;m=d['motor']
    m['thermal']=dict(reference_c=20.,ambient_c=20.,alpha_per_c=.00393,stator_capacity_j_per_k=100.,rotor_capacity_j_per_k=80.,stator_ambient_w_per_k=1.,rotor_ambient_w_per_k=.8,cross_w_per_k=.5,maximum_c=160.)
    m['provenance']['thermal']='authored two winding RC lumps; nominal copper alpha20=0.00393/K; not calibrated cooling or thermal overload certification'
    d['motor_temperature_trip_c']=[140.,140.]
    if name=='warm_winding':d['motor_initial_temperature_c']=[90.,70.]
    if name=='stator_trip':
        m['thermal'].update(stator_capacity_j_per_k=10.,stator_ambient_w_per_k=.01,rotor_ambient_w_per_k=.01,cross_w_per_k=.01)
        d.update(motor_initial_temperature_c=[80.,50.],motor_temperature_trip_c=[80.02,140.]);d.pop('motor_initial_fluxes_ab_vs',None)
    if name=='rotor_trip':
        m['thermal'].update(rotor_capacity_j_per_k=5.,stator_ambient_w_per_k=.01,rotor_ambient_w_per_k=.01,cross_w_per_k=.01)
        d.update(motor_initial_temperature_c=[50.,80.],motor_temperature_trip_c=[140.,80.02])
    if name in {'initial_trip_cooling','winding_cooling'}:
        d.update(motor_initial_temperature_c=[90.,60.],gate_block_windows=[(0.,.12)])
        if name=='initial_trip_cooling':d['motor_temperature_trip_c']=[85.,140.]
        m['thermal'].update(stator_capacity_j_per_k=2.,rotor_capacity_j_per_k=3.,stator_ambient_w_per_k=5.,rotor_ambient_w_per_k=4.,cross_w_per_k=2.)
    return SwitchingDriveStudy.model_validate(d).model_dump(mode='json')
