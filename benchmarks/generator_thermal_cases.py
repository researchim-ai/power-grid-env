"""Effective stator I-squared replica examples; no OEM relay equivalence."""
from pathlib import Path
from benchmarks.multigen_cases import example as multi
from power_grid_env.study_spec import StudySpec

NAMES=('constant_cold','constant_warm','equilibrium_hold','control_trip','manual_open','three_machine')
NEGATIVE=('initial_limit','unknown_target','duplicate_relay')


def example(name):
    if name not in (*NAMES,*NEGATIVE):raise ValueError('unknown stator thermal case')
    spec=multi('simultaneous3' if name=='three_machine' else 'governor2' if name=='control_trip' else 'hold2')
    data=spec.model_dump(mode='json');data['id']='generator-thermal-'+name;s=data['coupled']
    p=dict(generator_id='g2',time_constant_s=.1,trip_level=.003,initial_level=0.,initialization='specified')
    if name=='constant_warm':p['initial_level']=.0025
    if name=='equilibrium_hold':p.update(trip_level=.02,initialization='equilibrium')
    if name=='control_trip':p.update(trip_level=.008,time_constant_s=.08,initialization='equilibrium')
    if name=='manual_open':
        s['events']=[dict(time_s=.02,kind='generator_open',equipment_id='g2')]
    if name=='three_machine':p.update(generator_id='g3',trip_level=.012)
    if name=='initial_limit':p.update(initialization='equilibrium',trip_level=.001)
    if name=='unknown_target':p['generator_id']='missing'
    s['generator_thermal']=[p]
    if name=='duplicate_relay':s['generator_thermal'].append(dict(p))
    return StudySpec.model_validate(data)


if __name__=='__main__':
    from power_grid_env.report import write_json
    for name in (*NAMES,*NEGATIVE):write_json(Path('cases/studies/generator_thermal_'+name+'.json'),example(name).model_dump(mode='json'))
