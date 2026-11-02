"""Authored DOL starts from an unloaded, already energized generator/network."""
from pathlib import Path
from benchmarks.multibus_loaded_cases import example as loaded
from power_grid_env.study_spec import StudySpec

NAMES=('gen_hold','gen_simultaneous','gen_staggered','gen_lv_clock','grid_simultaneous')


def example(name):
    if name not in NAMES:raise ValueError('unknown prestart case')
    spec=loaded('gen_lv_clock' if name=='gen_lv_clock' else 'grid_hold' if name.startswith('grid_') else 'gen_hold')
    data=spec.model_dump(mode='json');data['id']='multibus-prestart-'+name
    s=data['coupled'] if spec.coupled else data['induction'];s['operating_point']=None
    s['duration_s']=.2 if name=='gen_hold' else 3. if name=='gen_simultaneous' else 1.2
    for m in s['motors']:m['connected']=False
    s['events']=[] if name=='gen_hold' else [dict(time_s=.1+(.1*i if name=='gen_staggered' else 0.),kind='motor_close',equipment_id=m['id']) for i,m in enumerate(s['motors'])]
    return StudySpec.model_validate(data)


if __name__=='__main__':
    from power_grid_env.report import write_json
    for name in NAMES:write_json(Path('cases/studies/multibus_prestart_'+name+'.json'),example(name).model_dump(mode='json'))
