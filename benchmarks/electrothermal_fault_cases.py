"""Owner-authored finite RLC motor-fault corpus; thermal constants are not OEM fits."""
import copy
from itertools import product

from benchmarks.phase_core_acceptance import document as seed
from power_grid_env.phase_research import PhaseStudy


def jobs():
    for cages, start, fault, feedback in product([1, 3], ['cold_start', 'hot_rotating'], ['ll', 'three_phase', 'repeated_ll'], [False, True]):
        id = f'c{cages}-{start}-{fault}-'+('feedback' if feedback else 'fixed_r')
        doc = seed('asymmetric_fault')
        doc['id'] = doc['case']['id'] = id
        case = doc['case']
        motor = case['motors'][0]
        p = motor['passport']
        p['cages'] = p['cages'][:1]+[dict(resistance_ohm=5.+i, leakage_h=.018+.004*i) for i in range(cages-1)]
        p['initial_speed_rad_s'] = 0. if start == 'cold_start' else 80.
        p['thermal'].update(reference_c=20., ambient_c=20., alpha_per_c=.00393 if feedback else 0.,
                            stator_capacity_j_per_k=30., rotor_capacity_j_per_k=20.,
                            stator_ambient_w_per_k=1., rotor_ambient_w_per_k=.8, cross_w_per_k=.5, maximum_c=180.)
        p['provenance']['thermal'] = 'owner://electrothermal-fault/v1: authored two-lump capacities/cooling; copper alpha20=.00393/K, fixed-R counterfactual alpha=0; not OEM thermal fit'
        if start == 'hot_rotating':
            motor.update(initial_temperature_c=[90., 70.], initial_temperature_provenance='owner://electrothermal-fault/v1: authored hot rotating initial condition, not a measured operating point',
                         initial_fluxes_ab_vs=[[.5, .1]]+[[.47, .08]]*cages)
        case['resistors'] = [r for r in case['resistors'] if r['id'] != 'fault']
        case['events'] = []
        endpoints = [('a', 'b')] if fault != 'three_phase' else [(n, '0') for n in 'abc']
        for i, (a, b) in enumerate(endpoints):
            name = 'fault'+str(i)
            case['resistors'].append(dict(id=name, frm=a, to=b, resistance_ohm=5000.))
            for t, r in [(.012, 2.), (.024, 5000.)]+([(.036, 2.), (.048, 5000.)] if fault == 'repeated_ll' else []):
                case['events'].append(dict(time_s=t, kind='resistance', equipment_id=name, value=r))
        doc['emt'].update(duration_s=.06, sample_s=.0005, max_step_s=.0002, rtol=1e-9, atol=1e-11)
        yield dict(id=id, cages=cages, start=start, fault=fault, feedback=feedback,
                   study=PhaseStudy.model_validate(doc).model_dump(mode='json'))
