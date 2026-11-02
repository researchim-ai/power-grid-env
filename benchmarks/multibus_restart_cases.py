"""Preregistered authored DOL/dip/coast/restart cases on one physical network."""
from pathlib import Path
from benchmarks.multibus_prestart_cases import example as prestart
from power_grid_env.study_spec import StudySpec

NAMES=('grid_restart','gen_restart','gen_lv_restart','manual_inhibit','restart_limit','thermal_inhibit')
DIP=1.8
RECOVERY=2.0


def example(name):
    if name not in NAMES:raise ValueError('unknown DOL/restart case')
    spec=prestart('grid_simultaneous' if name=='grid_restart' else 'gen_lv_clock' if name=='gen_lv_restart' else 'gen_simultaneous')
    data=spec.model_dump(mode='json');data['id']='multibus-restart-'+name
    s=data['coupled'] if spec.coupled else data['induction'];s['duration_s']=3.
    source=spec.network.sources[0].id
    s['events'] += [dict(time_s=DIP,kind='source_voltage',equipment_id=source,value=.4),dict(time_s=RECOVERY,kind='source_voltage',equipment_id=source,value=1.)]
    if name=='gen_lv_restart':
        for m in s['motors']:m['contactor']['minimum_off_time_s']=.45
    if name=='manual_inhibit':
        s['events'] += [dict(time_s=1.97,kind='motor_close',equipment_id='m'),dict(time_s=2.05,kind='motor_open',equipment_id='m')]
    if name=='thermal_inhibit':
        s['motors'][0]['thermal']=dict(time_constant_s=.1,trip_level=2.,initial_level=0.)
        s['events'].append(dict(time_s=2.15,kind='motor_close',equipment_id='m'))
    if name=='restart_limit':
        for m in s['motors']:m['contactor']['maximum_automatic_restarts']=1
        s['events'] += [dict(time_s=2.55,kind='source_voltage',equipment_id=source,value=.4),dict(time_s=2.7,kind='source_voltage',equipment_id=source,value=1.)]
    s['events'].sort(key=lambda e:e['time_s'])
    return StudySpec.model_validate(data)


if __name__=='__main__':
    from power_grid_env.report import write_json
    for name in NAMES:write_json(Path('cases/studies/multibus_restart_'+name+'.json'),example(name).model_dump(mode='json'))
