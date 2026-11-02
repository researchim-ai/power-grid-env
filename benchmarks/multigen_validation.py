"""Resumable preregistered multi-generator/motor external acceptance corpus."""
from pathlib import Path
import argparse,json,math
from benchmarks.multigen_cases import NAMES,NEGATIVE
from benchmarks.multigen_audit import LIMITS,trace_contract,invariant
from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.andes_multibus_motor import native_reference,identity
from benchmarks.multibus_motor_validation import compare_si,compare_trace
from benchmarks.multibus_loaded_validation import stationary_check,runup_check
from benchmarks.native_online_validation import STEPS,source_identity,qualify,channel_limits
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint

ROOT=Path(__file__).resolve().parents[1]


def load_case(name):
    if name not in (*NAMES,*NEGATIVE):raise ValueError('registered multi-generator case required')
    return StudySpec.model_validate_json((ROOT/'cases/studies'/('multigen_'+name+'.json')).read_text())


def compare_case(name,folder):
    read=lambda p:json.loads((folder/p).read_text());spec=load_case(name);primary=read('production.json');si=read('SI.json');natives=[read(f'andes-{h}.json') for h in STEPS]
    if not primary.get('completed') or any(not n.get('completed') for n in natives):raise ValueError('incomplete multigen trace')
    compare_si(spec,primary,si);steps=[compare_trace(spec,n,primary,si) for n in natives]
    result=stationary_check(spec,dict(primary=primary,SI=si,**{str(h):n for h,n in zip(STEPS,natives)})) if name in ('hold2','hold4') else qualify(steps)
    if name in ('hold2','hold4'):result['passed']=result['passed'] and all(all(e<=step['limits'][key] for key,e in step['errors'].items()) for step in steps)
    contracts={label:trace_contract(spec,trace,label.startswith('native')) for label,trace in [('primary',primary),('SI',si),*[(f'native-{h}',n) for h,n in zip(STEPS,natives)]]}
    q_error=0.
    for n,h in zip(natives,STEPS):
        if n['source_sha256']!=identity() or n['case_sha256']!=canonical_hash(spec) or n['step_s']!=h or n['backend_version']!='2.0.0' or n['code_sha256']!=package_fingerprint() or n['controller_commands_precomputed'] or not n['stock_generated_cache_unchanged']:raise ValueError('native multigen identity changed')
        init=n['initialization']
        if not math.isfinite(init['maximum_generator_initial_rhs']) or init['maximum_generator_initial_rhs']>1e-8 or not init['generator_states_preserved_during_motor_replacement']:raise ValueError('native multigen initial RHS failed')
        for g in spec.network.generators:
            delta=abs(init['generator_initial_q_var_by_id'][g.id]-si['initialization']['generator_initial_q_var'][g.id])
            if not math.isfinite(delta):raise ValueError('nonfinite initial generator Q')
            q_error=max(q_error,delta)
    row=dict(case_id=name,passed=bool(result['passed'] and all(c['passed'] for c in contracts.values()) and q_error<=LIMITS['initial_Q_agreement_var']),steps=steps,refinement=result,contracts=contracts,maximum_initial_Q_error_var=q_error)
    if name=='dol2':row.update(runup_check(spec.coupled,primary,si,natives[-1]));row['passed']=row['passed'] and row['runup_passed']
    return row


def reject_case(name,folder):
    spec=load_case(name);write_json(folder/'study.json',spec.model_dump(mode='json'));p=run_study(spec,isolate=True,wall_timeout_s=90.);write_json(folder/'production.json',p)
    independent=None;native_scope=None;native=None
    try:independent_circuit(spec)
    except ValueError as exc:independent=str(exc)
    try:native=native_reference(spec,STEPS[0],timeout_s=120.)
    except ValueError as exc:native_scope=str(exc)
    if native is not None:write_json(folder/'native.json',native)
    if name=='q_limit_second':passed=not p.get('completed') and p.get('reason')=='generator_initial_q_limit' and independent is not None and 'initial Q limit' in independent and native is not None and not native.get('completed') and 'initial Q limit: g2' in native.get('log_tail','')
    else:
        message={'duplicate_node':'distinct existing PV','missing_passport':'one to four','unknown_control':'control target'}[name]
        passed=not p.get('completed') and p.get('reason')=='coupled_scope' and independent is not None and message in independent and native_scope is not None and message in native_scope
    row=dict(case_id=name,passed=bool(passed),primary_reason=p.get('reason'),independent_reason=independent,native_scope_reason=native_scope,native_status=native.get('status') if native else None)
    write_json(folder/'comparison.json',row);return row


def run(out,batch_size=None):
    out=Path(out)
    if batch_size is not None and (not isinstance(batch_size,int) or batch_size<1):raise ValueError('positive batch size required')
    sources=source_identity();code=package_fingerprint();cases={n:canonical_hash(load_case(n)) for n in (*NAMES,*NEGATIVE)}
    protocol=dict(schema='power-grid-env.multigen/v1',cases=list(NAMES),negative_cases=list(NEGATIVE),case_sha256=cases,source_sha256=sources,code_sha256=code,native_steps_s=list(STEPS),native_timeout_s=300.,limits=LIMITS,channel_limits={n:channel_limits(load_case(n)) for n in NAMES},event_time_limit_s=1e-6,initial_generator_rhs_limit=1e-8,minimum_DOL_final_speed_pu=.95)
    if (out/'protocol.json').exists():
        if json.loads((out/'protocol.json').read_text())!=protocol:raise ValueError('resume requires identical original protocol, inputs, limits and producer')
    else:write_json(out/'protocol.json',protocol)
    rows=[];processed=0;write_json(out/'summary.json',dict(passed=False,status='running'))
    try:
        for name in NAMES:
            folder=out/name
            if (folder/'comparison.json').exists():
                cached=json.loads((folder/'comparison.json').read_text())
                if not cached.get('passed'):raise ValueError('failed cached case requires a new output directory')
                for file,h in cached['artifacts_sha256'].items():
                    if sha256_file(folder/file)!=h:raise ValueError('cached raw case changed: '+name+'/'+file)
                row=compare_case(name,folder)
                if not row['passed']:raise ValueError('cached case no longer qualifies')
                rows.append(cached);continue
            if batch_size is not None and processed>=batch_size:break
            spec=load_case(name);write_json(folder/'study.json',spec.model_dump(mode='json'))
            # Incomplete cases are recomputed in full; no partial trace counts.
            for h in STEPS:
                print('native',name,h,flush=True);n=native_reference(spec,h,timeout_s=300.);write_json(folder/f'andes-{h}.json',n)
                if not n.get('completed'):raise ValueError('native multigen incomplete: '+str(n.get('log_tail',n.get('status'))))
            p=run_study(spec,isolate=True,wall_timeout_s=90.);write_json(folder/'production.json',p)
            if not p.get('completed'):raise ValueError('primary multigen initialization/integration failed')
            si=independent_circuit(spec).run(p);write_json(folder/'SI.json',si);write_json(folder/'SI-comparison.json',compare_si(spec,p,si))
            row=compare_case(name,folder);row['artifacts_sha256']={p.name:sha256_file(p) for p in folder.glob('*.json') if p.name!='comparison.json'}
            write_json(folder/'comparison.json',row);rows.append(row);processed+=1;print('compared',name,row['passed'],flush=True)
            if not row['passed']:raise ValueError('multi-generator case exceeds registered limits: '+name)
        if sources!=source_identity() or code!=package_fingerprint() or any(canonical_hash(load_case(n))!=h for n,h in cases.items()):raise ValueError('multi-generator producer changed')
        if len(rows)<len(NAMES):
            summary=dict(passed=False,status='partial',completed_cases=[r['case_id'] for r in rows],remaining_cases=[n for n in NAMES if n not in {r['case_id'] for r in rows}],resume_supported=True)
        else:
            rejected=[reject_case(n,out/'negative'/n) for n in NEGATIVE];metamorphic=[]
            for name in ('permuted2','base2'):
                for file in ['production.json','SI.json',f'andes-{STEPS[-1]}.json']:
                    left=json.loads((out/'governor2'/file).read_text());right=json.loads((out/name/file).read_text())
                    metamorphic.append(dict(case=name,file=file,**invariant(load_case('governor2'),left,right)))
            summary=dict(passed=all(r['passed'] for r in [*rows,*rejected,*metamorphic]),status='complete',cases=rows,rejected_cases=rejected,metamorphic=metamorphic,native_runs=3*len(rows),source_sha256=sources,code_sha256=code,field_validated=False,independent_host=False)
            summary['artifacts_sha256']={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json')) if p!=out/'summary.json'}
        if sources!=source_identity() or code!=package_fingerprint() or any(canonical_hash(load_case(n))!=h for n,h in cases.items()):raise ValueError('producer changed before multigen commit')
        write_json(out/'summary.json',summary);return summary
    except Exception as exc:
        write_json(out/'summary.json',dict(passed=False,status='failed',completed_cases=[r['case_id'] for r in rows],error=str(exc)));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--batch-size',type=int);a=p.parse_args();r=run(a.out,a.batch_size)
    if r['status']=='complete' and not r['passed']:raise SystemExit(1)
