"""20 physical scenarios,40 search-seed configurations and12 initial rejections."""
import copy
from pathlib import Path
from benchmarks.motor_only_loaded_protection import jobs as protected_jobs
from power_grid_env.study_artifacts import read_json
from power_grid_env.study_spec import StudySpec

ROOT=Path(__file__).resolve().parents[1]


def jobs():
    protocol=read_json(ROOT/'cases/motor-only-loaded/protocol.json')
    for old in protected_jobs(protocol):
        if old['method']!='network_dae' or old['grid']!=0 or old['thermal']!='equilibrium':continue
        for seed in [(20.,20.),(90.,70.)]:
            s=copy.deepcopy(old['study']);c=s['coupled']
            c['operating_point'].update(winding_state='equilibrium',thermal_state='equilibrium')
            c['motors'][0]['winding_thermal']=dict(reference_c=20.,ambient_c=20.,alpha_per_c=.00393,initial_temperature_c=list(seed),
                stator_capacity_j_per_k=3000.,rotor_capacity_j_per_k=2000.,stator_ambient_w_per_k=30.,rotor_ambient_w_per_k=24.,cross_w_per_k=6.,maximum_c=180.,
                provenance='owner://rms-electrothermal-equilibrium/v1: authored passive two-node RC and copper alpha; temperatures are root search seeds,not imposed measurements')
            kind=old['kind']+('-uv' if old['uv'] else '-no-uv')
            id=old['condition_id']+'-'+kind+'-seed'+str(int(seed[0]))
            yield dict(id=id,condition=old['condition_id'],kind=kind,seed=list(seed),study=StudySpec.model_validate(s).model_dump(mode='json'))
            if old['kind']=='primary' and old['uv']:
                c.update(faults=[],events=[],duration_s=5.,step_s=.02)
                yield dict(id=old['condition_id']+'-hold-seed'+str(int(seed[0])),condition=old['condition_id'],kind='hold',seed=list(seed),study=StudySpec.model_validate(s).model_dump(mode='json'))


def negatives():
    base=next(j['study'] for j in jobs())
    for kind,reason in [('cooling','underdetermined_winding_thermal_network'),('ceiling','no_qualified_electrothermal_equilibrium'),
                        ('stall','no_qualified_electrothermal_equilibrium'),('passport','electrothermal_equilibrium_requires_relay_replica_equilibrium'),
                        ('disconnected','electrothermal_equilibrium_requires_1_to_32_connected_single_cage_thermal_motors'),
                        ('missing','electrothermal_equilibrium_requires_1_to_32_connected_single_cage_thermal_motors')]:
        s=copy.deepcopy(base);m=s['coupled']['motors'][0];h=m['winding_thermal']
        if kind=='cooling':h.update(stator_ambient_w_per_k=0.,rotor_ambient_w_per_k=0.,cross_w_per_k=0.)
        elif kind=='ceiling':h.update(maximum_c=30.,initial_temperature_c=[20.,20.])
        elif kind=='stall':m['fan_torque_pu']=1000.
        elif kind=='passport':s['coupled']['operating_point']['thermal_state']='passport'
        elif kind=='disconnected':m['connected']=False
        else:m.pop('winding_thermal')
        for method in ['radau','network_dae']:
            owner=copy.deepcopy(s);owner['coupled']['integration_method']=method
            yield dict(id='negative-'+kind+'-'+method,study=StudySpec.model_validate(owner).model_dump(mode='json'),reason=reason)
