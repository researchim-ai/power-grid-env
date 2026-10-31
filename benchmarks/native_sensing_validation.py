"""Re-evaluate true contactor sensing on the sealed native physical corpus."""
from pathlib import Path
import argparse,json
import numpy as np
from benchmarks.native_terminal_measurements import reconstruct_terminals
from benchmarks.andes_joint_double_cage import sample_native
from power_grid_env.study_spec import StudySpec
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint

BASELINE=Path('reports/native-motor-group-final-2026-10-31')
VOLTAGE_LIMIT_PU=1e-4
POWER_LIMIT_W_VAR=1000.


def qualify_case(rows):
    if [r['step_s'] for r in rows]!=[.00025,.000125,.0000625]:
        raise ValueError('complete ordered native refinement required')
    normalized=[]
    for row in rows:
        if set(row['maximum_errors'])!=set(row['limits']) or not row['limits']:
            raise ValueError('sensing channel inventory mismatch')
        if any(not np.isfinite(v) or v<=0 for v in row['limits'].values()) or any(not np.isfinite(v) or v<0 for v in row['maximum_errors'].values()):
            raise ValueError('invalid sensing error or limit')
        normalized.append(max(row['maximum_errors'][k]/v for k,v in row['limits'].items()))
    converged=normalized[-1]<normalized[0]
    return dict(case_id=rows[0]['case_id'],passed=bool(normalized[-1]<=1 and converged),
                maximum_normalized_errors_by_step=normalized,finest_within_limits=normalized[-1]<=1,
                convergence_passed=converged)


def validate(out):
    out=Path(out);delivery=json.loads((BASELINE/'delivery.json').read_text())
    if delivery['code_sha256']!=package_fingerprint():raise ValueError('native corpus production code changed')
    for name,h in delivery['artifacts_sha256'].items():
        if sha256_file(BASELINE/name)!=h:raise ValueError('native artifact integrity: '+name)
    core=json.loads((BASELINE/'summary.json').read_text());extra=json.loads((BASELINE/'cold-grid-extension/summary.json').read_text());rows=[]
    write_json(out/'summary.json',dict(passed=False,status='running'))
    for case in [*core['cases'],*extra['cases']]:
        name=case['case_id'].removeprefix('automatic-group-');folder=(BASELINE/'cold-grid-extension'/name) if name=='grid_cold' else BASELINE/name
        spec=StudySpec.model_validate_json((folder/'study.json').read_text());commands=StudySpec.model_validate_json((folder/'command-study.json').read_text())
        actual=json.loads((folder/'production.json').read_text());proof=json.loads((folder/'independent-control.json').read_text());settings=spec.coupled if spec.coupled else spec.induction
        alignment={p['production_time_s']:p['independent_time_s'] for p in proof['matched_event_samples']}
        times=[alignment.get(t,t) if side in {'pre_event','post_event'} else t for t,side in zip(actual['time_s'],actual['sample_side'])]
        for step in case['steps']:
            native=json.loads((folder/f"andes-{step['step_s']}.json").read_text());observer=reconstruct_terminals(commands,native)
            errors={};limits={};wrong={};comparison_channels={}
            for m in settings.motors:
                if not m.contactor:continue
                key=m.id+'.contactor_sensed_voltage_pu';values=observer['node_voltage_pu'][m.contactor.sense_node_id]
                trace=dict(time_s=observer['time_s'],sample_side=observer['sample_side'],channels={key:values})
                sampled=sample_native(trace,times,actual['sample_side'],key);comparison_channels[key]=sampled.tolist()
                errors[key]=float(np.max(abs(sampled-actual['channels'][key])));limits[key]=VOLTAGE_LIMIT_PU
                source_node=next(t.connectivity_node_id for t in spec.network.terminals if t.id==spec.network.sources[0].terminal_id)
                if m.contactor.sense_node_id==source_node:
                    trace['channels'][key]=observer['channels']['source.internal_emf_voltage_pu']
                    ideal=sample_native(trace,times,actual['sample_side'],key)
                    wrong[key]=float(np.max(abs(ideal-actual['channels'][key])))
            source=spec.network.sources[0]
            for key in ['p_w','q_var']:
                target=source.id+'.'+key
                if target in actual['channels']:
                    trace=dict(time_s=observer['time_s'],sample_side=observer['sample_side'],channels={target:observer['channels']['source.'+key]})
                    sampled=sample_native(trace,times,actual['sample_side'],target);comparison_channels[target]=sampled.tolist()
                    errors[target]=float(np.max(abs(sampled-actual['channels'][target])));limits[target]=POWER_LIMIT_W_VAR
            passed=bool(errors and all(np.isfinite(errors[k]) and errors[k]<=limit for k,limit in limits.items()))
            row=dict(case_id=case['case_id'],step_s=step['step_s'],within_limits=passed,maximum_errors=errors,limits=limits,wrong_internal_emf_errors_pu=wrong,
                     wrong_internal_emf_rejected=bool(any(e>VOLTAGE_LIMIT_PU for e in wrong.values())),maximum_source_closure_pu=observer['maximum_source_closure_pu'],
                     native_input_sha256=sha256_file(folder/f"andes-{step['step_s']}.json"),command_study_sha256=sha256_file(folder/'command-study.json'),production_input_sha256=sha256_file(folder/'production.json'),native_closed_loop_controller_qualified=False,field_validated=False,industrial_profile_qualified=False)
            target=out/name/str(step['step_s']);write_json(target/'native-terminals.json',observer)
            write_json(target/'comparison.json',row);write_json(target/'aligned-native-measurements.json',dict(time_s=actual['time_s'],sample_side=actual['sample_side'],channels=comparison_channels))
            rows.append(row)
        print(name,qualify_case([r for r in rows if r['case_id']==case['case_id']])['passed'],flush=True)
    qualifications=[qualify_case([r for r in rows if r['case_id']==case['case_id']]) for case in [*core['cases'],*extra['cases']]]
    result=dict(passed=bool(len(rows)==48 and len(qualifications)==16 and all(r['passed'] for r in qualifications) and any(r['wrong_internal_emf_rejected'] for r in rows)),case_qualifications=qualifications,cases=rows,native_waveforms_reused=48,new_native_integrations=0,
                baseline_delivery=str(BASELINE/'delivery.json'),baseline_delivery_sha256=sha256_file(BASELINE/'delivery.json'),code_sha256=package_fingerprint(),
                source_sha256={p:sha256_file(Path(p)) for p in ['benchmarks/native_terminal_measurements.py','benchmarks/native_sensing_validation.py','benchmarks/andes_joint_double_cage.py','benchmarks/andes_group_motor_reference.py']},
                native_source_terminals_reconstructed=True,native_closed_loop_controller_qualified=False,field_validated=False,industrial_profile_qualified=False)
    write_json(out/'summary.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    r=validate(a.out)
    if not r['passed']:raise SystemExit(1)
