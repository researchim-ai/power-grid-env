"""Authored two-cage common-rotor-temperature modes and cage-label controls."""
import copy
from itertools import product
from pathlib import Path
from power_grid_env.study_spec import StudySpec
from power_grid_env.study_artifacts import read_json
ROOT=Path(__file__).resolve().parents[1]


def jobs():
    for mode,mutual,kind in product(['cold','passport','equilibrium'],[0.,.02],['hold','primary','breaker_failure','measurement_loss','primary-no-uv']):
        source='primary-no-uv' if kind=='primary-no-uv' else (kind+'-uv' if kind!='hold' else 'primary-uv')
        s=read_json(ROOT/f'cases/motor-only-loaded/protected/{source}.json');c=s['coupled'];m=c['motors'][0]
        m.update(model='double_cage',rotor2_r_pu=.06,rotor2_x_pu=.03,rotor_mutual_x_pu=mutual)
        m['winding_thermal']=dict(reference_c=20.,ambient_c=20.,alpha_per_c=.00393,initial_temperature_c=[90.,70.],
            stator_capacity_j_per_k=3000.,rotor_capacity_j_per_k=2000.,stator_ambient_w_per_k=30.,rotor_ambient_w_per_k=24.,cross_w_per_k=6.,maximum_c=180.,
            provenance='owner://two-cage-winding/v1: authored second cage/rotor mutual and RC;common rotor temperature and copper alpha,not independently measured cage hotspots')
        c['operating_point']=None if mode=='cold' else dict(method='high_speed_equilibrium',thermal_state='equilibrium',winding_state=mode,minimum_speed_pu=.8)
        if kind=='hold':c.update(faults=[],events=[],duration_s=5. if mode=='equilibrium' else .6)
        id=f'{mode}-mutual{mutual:.2f}-{kind}'
        yield dict(id=id,mode=mode,mutual=mutual,kind=kind,permuted=False,study=StudySpec.model_validate(s).model_dump(mode='json'))
        if mutual==.02 and kind=='primary':
            owner=copy.deepcopy(s);m=owner['coupled']['motors'][0]
            m['rotor_r_pu'],m['rotor2_r_pu']=m['rotor2_r_pu'],m['rotor_r_pu'];m['rotor_x_pu'],m['rotor2_x_pu']=m['rotor2_x_pu'],m['rotor_x_pu']
            yield dict(id=id+'-permuted',mode=mode,mutual=mutual,kind=kind,permuted=True,study=StudySpec.model_validate(owner).model_dump(mode='json'))


def negatives():
    s=next(j['study'] for j in jobs() if j['mode']=='equilibrium')
    for kind,reason in [('cooling','underdetermined_winding_thermal_network'),('ceiling','no_qualified_electrothermal_equilibrium'),
        ('stall','no_qualified_electrothermal_equilibrium'),('passport','electrothermal_equilibrium_requires_relay_replica_equilibrium'),
        ('disconnected','electrothermal_equilibrium_requires_1_to_32_connected_thermal_cage_motors'),('missing','electrothermal_equilibrium_requires_1_to_32_connected_thermal_cage_motors')]:
        owner=copy.deepcopy(s);m=owner['coupled']['motors'][0];h=m['winding_thermal']
        if kind=='cooling':h.update(stator_ambient_w_per_k=0.,rotor_ambient_w_per_k=0.,cross_w_per_k=0.)
        elif kind=='ceiling':h.update(initial_temperature_c=[20.,20.],maximum_c=30.)
        elif kind=='stall':m['fan_torque_pu']=1000.
        elif kind=='passport':owner['coupled']['operating_point']['thermal_state']='passport'
        elif kind=='disconnected':m['connected']=False
        else:m.pop('winding_thermal')
        for method in ['radau','network_dae']:
            study=copy.deepcopy(owner);study['coupled']['integration_method']=method
            yield dict(id='negative-'+kind+'-'+method,study=StudySpec.model_validate(study).model_dump(mode='json'),reason=reason)
