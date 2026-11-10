"""Extended physical plants with an explicitly declared nominal FOC observer."""
import copy
from benchmarks.switching_drive_cases import document as base
from power_grid_env.switching_drive import SwitchingDriveStudy
NAMES=('core_single','double_cage','four_cage_core','saturated_core','saturated_block','saturated_regeneration','saturated_four_cage')


def document(name):
    if name not in NAMES:raise ValueError('unknown extended switching study')
    d=copy.deepcopy(base('regeneration' if name=='saturated_regeneration' else 'foc_start'))
    d.update(id=name,duration_s=.12);d['observer_motor']=copy.deepcopy(d['motor']);m=d['motor']
    m['provenance']['electrical']+='; added cage/core values are authored, not measured'
    if name!='double_cage':m['core_loss_s']=.006;m['provenance']['core_loss']='authored 0.006 S eddy-current conductance; not an iron-loss map'
    if name in {'double_cage','four_cage_core','saturated_block','saturated_four_cage'}:
        for k in range(3 if name in {'four_cage_core','saturated_four_cage'} else 1):m['cages'].append(dict(resistance_ohm=5.+k,leakage_h=.018+.004*k))
    if name.startswith('saturated'):
        m['saturation_beta_per_vs']=2.;m['saturation_exponent']=7;m['provenance']['magnetics']='published radial power-law form; authored beta=2/Vs, exponent=7'
    if len(m['cages'])>1:
        nominal=d['observer_motor'];nominal['cages']=[dict(resistance_ohm=1/sum(1/c['resistance_ohm'] for c in m['cages']),leakage_h=1/sum(1/c['leakage_h'] for c in m['cages']))]
        nominal['provenance']['electrical']='authored nominal reduction: parallel low-frequency rotor resistance and high-frequency leakage; not an exact spectrum equivalent'
        ratio=nominal['cages'][0]['leakage_h']/m['cages'][0]['leakage_h'];d['control']['current_kp_ohm']*=ratio;d['control']['current_ki_ohm_per_s']*=ratio
    if name.startswith('saturated'):d['motor_initial_fluxes_ab_vs']=[[.6,0.]]+[[.58,0.] for _ in m['cages']]
    if name=='saturated_block':d['gate_block_windows']=[(.07,.08)]
    return SwitchingDriveStudy.model_validate(d).model_dump(mode='json')
