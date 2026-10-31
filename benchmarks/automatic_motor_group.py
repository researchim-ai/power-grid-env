"""Independent mixed-cage motor-group physics, voltage contactors and thermal ties."""
from pathlib import Path
import argparse
import numpy as np
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint
from benchmarks.double_cage_reference import reference,KEYS
from benchmarks.independent_group_contactor import run as hybrid
from benchmarks.group_si_generator import Generator

NAMES=('grid_staggered','grid_simultaneous','grid_feedback','grid_manual_inhibit','grid_thermal_tie','grid_thermal_near',
       'generator_staggered','generator_simultaneous','generator_feedback','generator_thermal_tie','generator_saturated','generator_cold','grid_scale32','generator_scale16','generator_frequency_base')
DISCRETE={'connected','thermal_latched','contactor_voltage_available','contactor_pending_restart','contactor_restart_count','contactor_timer_active'}
EXTRA=['cage1_e_real_pu','cage1_e_imag_pu','cage2_e_real_pu','cage2_e_imag_pu','rotor1_current_pu','rotor2_current_pu','rotor1_copper_loss_w','rotor2_copper_loss_w']


def example(name):
    if name not in NAMES:raise ValueError('unknown group case')
    return StudySpec.model_validate_json(Path('cases/studies/automatic_group_'+name+'.json').read_text())


def signature(e):
    if e['kind'].startswith('contactor_') or e['kind']=='thermal_trip':return e['kind'],e['equipment_id'],e.get('outcome')
    if e['kind'] in {'motor_open','motor_close'}:return e['kind'],e['equipment_id'],e.get('cause','manual'),e.get('outcome')
    return None


def compare(spec,out=None):
    s=spec.coupled if spec.study=='coupled_rms' else spec.induction
    if len(s.motors)<2:raise ValueError('motor group required')
    actual=run_study(spec,isolate=True,wall_timeout_s=90.)
    if not actual.get('completed'):raise ValueError('production group incomplete: '+str(actual))
    independent=reference(spec,actual,hybrid_runner=hybrid,generator_factory=Generator if spec.study=='coupled_rms' else None)
    channels={}
    for m in s.motors:
        keys=KEYS+(EXTRA if m.model=='double_cage' else [])
        channels.update({m.id+'.'+key:independent['channels'][m.id][:,j].tolist() for j,key in enumerate(keys)})
        if m.thermal:channels[m.id+'.thermal_level']=independent['thermal'][m.id]
    channels.update(independent['control_channels']);channels.update(independent.get('generator_channels',{}))
    errors={k:float(max(abs(np.array(v)-actual['channels'][k]))) for k,v in channels.items()}
    def limit(key):
        if key.split('.')[-1] in DISCRETE:return 0.
        if key.endswith(('.p_w','.q_var','copper_loss_w')):return 100.
        if key.endswith('contactor_timer_remaining_s'):return 1e-6
        if key.endswith(('.omega','.speed_pu','.delta','.bus_voltage_pu','contactor_sensed_voltage_pu')):return 1e-5
        return 1e-4
    limits={k:limit(k) for k in channels}
    wanted=[e for e in independent['independent_event_log'] if signature(e) is not None];observed=[e for e in actual['event_log'] if signature(e) is not None]
    identical=[signature(e) for e in wanted]==[signature(e) for e in observed]
    time_error=max([abs(a['time_s']-b['time_s']) for a,b in zip(wanted,observed)],default=0.) if identical else None
    result=dict(case_id=spec.id,passed=bool(identical and time_error<=1e-6 and all(np.isfinite(errors[k]) and errors[k]<=v for k,v in limits.items())),motor_count=len(s.motors),generator_count=len(spec.network.generators),
        maximum_errors=errors,limits=limits,events_match=identical,maximum_event_time_error_s=time_error,event_time_tolerance_s=1e-6,expected_events=wanted,
        observed_events=[{k:v for k,v in e.items() if k in {'kind','time_s','equipment_id','cause','outcome'}} for e in observed],
        field_validated=False,industrial_profile_qualified=False,native_group_simulator_qualified=False)
    if out is not None:
        out=Path(out);write_json(out/'study.json',spec.model_dump(mode='json'));write_json(out/'production.json',actual)
        write_json(out/'independent.json',dict(time_s=actual['time_s'],sample_side=actual['sample_side'],channels=channels,independent_event_log=wanted))
        result['artifacts_sha256']={p.name:sha256_file(p) for p in out.glob('*.json') if p.name!='comparison.json'};write_json(out/'comparison.json',result)
    return result


def rejected_case(out=None):
    spec=StudySpec.model_validate_json(Path('cases/studies/automatic_group_generator_cold_q_limit.json').read_text())
    actual=run_study(spec,isolate=True,wall_timeout_s=90.);oracle_reason=None
    try:reference(spec,dict(time_s=[0.],sample_side=['initial']),hybrid_runner=hybrid,generator_factory=Generator)
    except ValueError as e:oracle_reason=str(e)
    passed=actual.get('completed') is False and actual.get('reason')=='generator_initial_q_limit' and oracle_reason=='independent group generator initial Q limit'
    result=dict(passed=passed,case_id=spec.id,expected_rejection='generator_initial_q_limit',production_reason=actual.get('reason'),independent_reason=oracle_reason)
    if out is not None:
        out=Path(out);write_json(out/'study.json',spec.model_dump(mode='json'));write_json(out/'production.json',actual);write_json(out/'comparison.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    write_json(a.out/'summary.json',dict(passed=False,status='running'));rows=[]
    try:
        for name in NAMES:
            r=compare(example(name),a.out/name);rows.append(r);print(name,r['passed'],r['maximum_event_time_error_s'],flush=True)
        rejected=rejected_case(a.out/'generator_cold_q_limit')
        result=dict(passed=bool(all(r['passed'] for r in rows) and rejected['passed']),cases=rows,rejected_case=rejected,code_sha256=package_fingerprint(),
            source_sha256={str(p):sha256_file(p) for p in map(Path,[__file__,'benchmarks/double_cage_reference.py','benchmarks/independent_group_contactor.py','benchmarks/group_si_generator.py','benchmarks/induction_reference.py','benchmarks/genrou_reference.py'])},
            case_files_sha256={str(p):sha256_file(p) for p in [Path('cases/studies/automatic_group_'+n+'.json') for n in [*NAMES,'generator_cold_q_limit']]},
            independent_group_decisions=True,production_control_decisions_as_oracle=False,native_group_simulator_qualified=False,field_validated=False,industrial_profile_qualified=False)
    except Exception as exc:
        write_json(a.out/'summary.json',dict(passed=False,status='failed',error=str(exc)[:3000],cases=rows));raise
    write_json(a.out/'summary.json',result)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
