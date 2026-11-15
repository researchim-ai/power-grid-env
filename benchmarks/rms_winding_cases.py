"""Authored RMS winding feedback corpus, matched hot-initial frozen controls."""
import copy
from itertools import product
from pathlib import Path
from power_grid_env.study_artifacts import read_json
from power_grid_env.study_spec import StudySpec

ROOT=Path(__file__).resolve().parents[1]


def jobs():
    for kind,initial,temperatures,feedback in product(['primary-uv','breaker_failure-uv','measurement_loss-uv','primary-no-uv'], ['cold','loaded'],[(20.,20.),(90.,70.)],[False,True]):
        study=read_json(ROOT/f'cases/motor-only-loaded/protected/{kind}.json')
        c=study['coupled'];m=c['motors'][0]
        if initial=='cold':c['operating_point']=None
        h=dict(reference_c=20.,ambient_c=20.,alpha_per_c=.00393,initial_temperature_c=list(temperatures),
            stator_capacity_j_per_k=3000.,rotor_capacity_j_per_k=2000.,stator_ambient_w_per_k=10.,rotor_ambient_w_per_k=8.,cross_w_per_k=2.,maximum_c=180.,
            provenance='owner://rms-winding/v1: authored RC/initial winding temperatures; shared nominal copper alpha=.00393/K, not identified OEM material/thermal parameters')
        if not feedback:
            m['stator_r_pu']*=1+h['alpha_per_c']*(temperatures[0]-h['reference_c'])
            m['rotor_r_pu']*=1+h['alpha_per_c']*(temperatures[1]-h['reference_c'])
            h['alpha_per_c']=0.
        m['winding_thermal']=h
        id=f'{kind}-{initial}-T{int(temperatures[0])}-{int(temperatures[1])}-'+('feedback' if feedback else 'frozen')
        yield dict(id=id,kind=kind,initial=initial,temperatures=list(temperatures),feedback=feedback,
                   study=StudySpec.model_validate(study).model_dump(mode='json'))
