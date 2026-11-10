"""Authored matched/mismatched observer studies; no OEM parameter claim."""
import copy
from benchmarks.switching_extended_cases import document as base
from power_grid_env.switching_drive import SwitchingDriveStudy

NAMES=('double_cage_matched','four_cage_saturated','four_cage_mismatch','saturated_block')


def document(name):
    if name not in NAMES:raise ValueError('unknown observer case')
    d=base('double_cage' if name=='double_cage_matched' else 'saturated_four_cage')
    d.update(id=name,observer_mode='multicage_current',duration_s=.08,sample_s=.0002)
    d.pop('motor_initial_fluxes_ab_vs',None)
    d['motor'].pop('core_loss_s',None)
    observer=copy.deepcopy(d['motor']);observer['id']+='-observer'
    if name=='four_cage_mismatch':
        for cage in observer['cages']:cage['resistance_ohm']*=1.3
        observer['provenance']['electrical']+='; deliberate +30% rotor R mismatch'
    d['observer_motor']=observer
    d['control']['torque_enable_flux_fraction']=.05
    if name=='saturated_block':d['gate_block_windows']=[(.05,.06)]
    d['provenance']['control']='authored saturated multicage current-model observer; sampled current/speed only; nominal R, no core-current separation'
    return SwitchingDriveStudy.model_validate(d).model_dump(mode='json')
