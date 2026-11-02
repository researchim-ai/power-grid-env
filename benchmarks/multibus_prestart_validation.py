"""Preregistered three-method prestart/DOL continuity and current-closure audit."""
from pathlib import Path
import argparse,json,math
import numpy as np
from benchmarks.multibus_prestart_cases import NAMES
from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.andes_multibus_motor import native_reference
from benchmarks.multibus_motor_validation import compare_si,compare_trace
from benchmarks.multibus_loaded_validation import stationary_check,runup_check
from benchmarks.multibus_loaded_audit import current_closure
from benchmarks.native_online_validation import STEPS,source_identity,qualify
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.model import canonical_hash
from power_grid_env.evidence import package_fingerprint

ROOT=Path(__file__).resolve().parents[1]


def load_case(name):
    if name not in NAMES:raise ValueError('unknown prestart case')
    return StudySpec.model_validate_json((ROOT/'cases/studies'/('multibus_prestart_'+name+'.json')).read_text())


def start_contract(spec,trace,*,native=False):
    """Check actual ports and differential continuity, including event sides."""
    s=spec.coupled if spec.coupled else spec.induction
    times=np.asarray(trace['time_s']);sides=trace['sample_side'];ch=trace['channels']
    first=min((e.time_s for e in s.events),default=s.duration_s+1)
    before=(times<first)|((times==first)&(np.asarray(sides)=='pre_event'))
    keys=[m.id+'.'+k for m in s.motors for k in ('speed_pu','e_real_pu','e_imag_pu')]
    keys += [g.id+'.'+k for g in spec.network.generators for k in ('delta','omega','e1q','e1d','e2d','e2q','TGOV1.valve_pu','SEXS.field_pu')]
    drift=max((float(np.max(abs(np.asarray(ch[k])[before]-ch[k][0]))) for k in keys),default=0.)
    zero=max((float(np.max(abs(np.asarray(ch[m.id+'.'+k])[before]))) for m in s.motors for k in ('current_pu','speed_pu','connected')),default=0.)
    jumps=[];inrush=[]
    for t in sorted(set(e.time_s for e in s.events)):
        pre=[i for i,(at,side) in enumerate(zip(times,sides)) if abs(at-t)<1e-10 and side=='pre_event']
        post=[i for i,(at,side) in enumerate(zip(times,sides)) if abs(at-t)<1e-10 and side=='post_event']
        if len(pre)!=1 or len(post)!=1:raise ValueError('missing unique pre/post energization samples')
        a,b=pre[0],post[0];jumps.extend(abs(ch[k][b]-ch[k][a]) for k in keys)
        for e in s.events:
            if e.time_s!=t:continue
            if ch[e.equipment_id+'.connected'][a]!=0 or ch[e.equipment_id+'.connected'][b]!=1:raise ValueError('motor did not energize at authored close')
            inrush.append(ch[e.equipment_id+'.current_pu'][b])
    jump=max(jumps,default=0.)
    kcl=current_closure(spec,trace) if native or 'node_voltage' in trace else None
    numbers=[drift,zero,jump,*inrush,*([] if kcl is None else [kcl])]
    passed=all(math.isfinite(v) for v in numbers) and drift<=1e-8 and zero<=1e-10 and jump<=1e-10 and all(v>1. for v in inrush) and (kcl is None or kcl<=1e-6)
    return dict(passed=bool(passed),maximum_prestart_state_drift=drift,maximum_disconnected_current_speed_status=zero,maximum_differential_state_jump=jump,closing_currents_pu=inrush,maximum_physical_KCL_pu=kcl)


def run(out,names=NAMES):
    out=Path(out)
    if (out/'protocol.json').exists():raise ValueError('use a fresh prestart output directory')
    if not names or len(set(names))!=len(names) or any(n not in NAMES for n in names):raise ValueError('registered unique prestart cases required')
    sources=source_identity();code=package_fingerprint();hashes={n:canonical_hash(load_case(n)) for n in names}
    write_json(out/'protocol.json',dict(schema='power-grid-env.multibus-prestart/v1',cases=list(names),case_sha256=hashes,source_sha256=sources,code_sha256=code,native_steps_s=list(STEPS),native_timeout_s=240.,prestart_state_drift_limit=1e-8,disconnected_current_speed_status_limit=1e-10,differential_state_jump_limit=1e-10,minimum_closing_current_pu=1.,physical_KCL_limit_pu=1e-6,generator_initial_rhs_limit=1e-8,initial_generator_Q_agreement_limit_var=.01,event_time_limit_s=1e-6,runup_case='gen_simultaneous',runup_minimum_final_speed_pu=.95))
    rows=[];write_json(out/'summary.json',dict(passed=False,status='running'))
    try:
        for name in names:
            spec=load_case(name);folder=out/name;write_json(folder/'study.json',spec.model_dump(mode='json'))
            natives=[]
            for h in STEPS:
                print('native',name,h,flush=True);n=native_reference(spec,h,timeout_s=240.);write_json(folder/f'andes-{h}.json',n)
                if not n.get('completed'):raise ValueError('incomplete native prestart: '+str(n.get('log_tail',n.get('status'))))
                if not n['stock_generated_cache_unchanged'] or n['controller_commands_precomputed'] or not n['initialization']['generator_states_preserved_during_motor_replacement']:raise ValueError('native prestart provenance failed')
                if n['initialization']['maximum_generator_initial_rhs']>1e-8:raise ValueError('native prestart generator RHS exceeded')
                natives.append(n)
            primary=run_study(spec,isolate=True,wall_timeout_s=90.);write_json(folder/'production.json',primary)
            if not primary.get('completed'):raise ValueError('primary prestart failed')
            si=independent_circuit(spec).run(primary);write_json(folder/'SI.json',si);write_json(folder/'SI-comparison.json',compare_si(spec,primary,si))
            steps=[compare_trace(spec,n,primary,si) for n in natives]
            if name=='gen_hold':
                result=stationary_check(spec,dict(primary=primary,SI=si,**{str(h):n for h,n in zip(STEPS,natives)}))
                result['passed']=result['passed'] and all(all(v<=step['limits'][k] for k,v in step['errors'].items()) for step in steps)
            else:result=qualify(steps)
            contracts={label:start_contract(spec,trace,native=label.startswith('native')) for label,trace in [('primary',primary),('SI',si),*[(f'native-{h}',n) for h,n in zip(STEPS,natives)]]}
            q_errors=[abs(n['initialization']['generator_initial_q_var']-si['initialization']['generator_initial_q_var']) for n in natives] if spec.coupled else []
            row=dict(case_id=name,steps=steps,comparison=result,contracts=contracts,initial_Q_errors_var=q_errors)
            row['passed']=bool(result['passed'] and all(c['passed'] for c in contracts.values()) and all(v<=.01 for v in q_errors))
            if name=='gen_simultaneous':
                row.update(runup_check(spec.coupled,primary,si,natives[-1]));row['passed']=row['passed'] and row['runup_passed']
            write_json(folder/'comparison.json',row);rows.append(row);print('compared',name,row['passed'],flush=True)
            if source_identity()!=sources or package_fingerprint()!=code or any(canonical_hash(load_case(n))!=h for n,h in hashes.items()):raise ValueError('prestart producer changed during corpus')
            write_json(out/'summary.json',dict(passed=False,status='running',cases=rows))
        summary=dict(passed=all(r['passed'] for r in rows),status='complete',full_registered_corpus=list(names)==list(NAMES),cases=rows,native_runs=3*len(rows),source_sha256=sources,code_sha256=code,field_validated=False,industrial_profile_qualified=False,independent_host_replay=False)
        summary['artifacts_sha256']={str(p.relative_to(out)):sha256_file(p) for p in sorted(out.rglob('*.json')) if p!=out/'summary.json'}
        write_json(out/'summary.json',summary);return summary
    except Exception as exc:
        write_json(out/'summary.json',dict(passed=False,status='failed',cases=rows,error=str(exc)));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));a=p.parse_args()
    if not run(a.out,a.cases)['passed']:raise SystemExit(1)
