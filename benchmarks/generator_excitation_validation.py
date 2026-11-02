"""Preregistered OEL full-SI corpus and separate stock-ANDES field-reaction corpus.

Native cases never contain feedback OEL; only GENROU field reconstruction is
validated against XadIfd. No native-OEL equivalence is asserted.
"""
from benchmarks.event_sampling import native_sample_sides
from pathlib import Path
import argparse,json,math
import numpy as np
from benchmarks.generator_excitation_cases import example,NAMES
from benchmarks.multibus_operating_point import independent_circuit
from benchmarks.generator_thermal_validation import full_compare,physical_contract
from benchmarks.andes_multibus_motor import native_reference
from benchmarks.andes_joint_double_cage import sample_native
from benchmarks.native_online_validation import source_identity,STEPS
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.evidence import package_fingerprint
from power_grid_env.model import canonical_hash
from power_grid_env.report import write_json,sha256_file

NATIVE=('hold','voltage_step','saturated')
NEGATIVE=('unknown','duplicate','initial_limit','classical')
LIMITS=dict(native_field_reaction_pu=5e-4,SI_state_pu=1e-5,SI_power=100.,event_time_s=1e-6,rotor_identity_pu=1e-10,convergence_floor_pu=5e-7)


def case(name):return example(name)


def baseline(name):
    s=case(name);s.id+='-without-OEL';s.coupled.overexcitation_limiters=[]
    return s


def negative(name):
    s=case('hold');s.id+='-invalid-'+name
    if name=='unknown':s.coupled.overexcitation_limiters[0].generator_id='absent'
    elif name=='duplicate':s.coupled.overexcitation_limiters*=2
    elif name=='initial_limit':s.coupled.overexcitation_limiters[0].current_limit_pu=.1
    elif name=='classical':
        from power_grid_env.rms_study import ClassicalMachine
        s.coupled.machines[1]=ClassicalMachine(generator_id='g2',rated_va=1e6,inertia_s=1.,x_d_prime_pu=.2)
    else:raise ValueError('unknown negative case')
    return s


def reaction_from_trace(spec,report,gid):
    """Reconstruct physical terminal current from P/Q, voltage and own VA base."""
    p=next(m for m in spec.coupled.machines if m.generator_id==gid);ch=report['channels'];prefix=gid+'.'
    get=lambda k:np.asarray(ch[prefix+k])
    v=get('terminal_voltage_pu')*np.exp(1j*get('terminal_angle_rad'))
    current=np.conj((get('p_w')+1j*get('q_var'))/p.rated_va/v)
    direct=-(current*np.exp(-1j*get('delta'))).imag
    residual=get('e1q')-get('e2d')-(p.x_d_prime_pu-p.leakage_pu)*direct
    return get('e1q')+(p.x_d_pu-p.x_d_prime_pu)*(direct+(p.x_d_prime_pu-p.x_d_subtransient_pu)/(p.x_d_prime_pu-p.leakage_pu)**2*residual)+get('Se')*get('psi2d')


def compare_native(spec,primary,native):
    if not native.get('completed'):raise ValueError('native run incomplete')
    if native.get('backend_version')!='2.0.0' or not native.get('stock_generated_cache_unchanged'):raise ValueError('native version/cache contract')
    if native['case_sha256']!=canonical_hash(spec) or native['code_sha256']!=package_fingerprint():raise ValueError('native producer/input mismatch')
    errors={}
    for g in spec.network.generators:
        values=np.asarray(native['field_reaction_reference'][g.id])
        if not np.isfinite(values).all():raise ValueError('nonfinite native field current')
        proxy=dict(native,channels={'reaction':values})
        sampled=np.asarray(sample_native(proxy,primary['time_s'],native_sample_sides(primary),'reaction'))
        expected=reaction_from_trace(spec,primary,g.id)
        errors[g.id]=float(max(abs(sampled-expected)))
    return dict(passed=all(v<=LIMITS['native_field_reaction_pu'] for v in errors.values()),errors=errors)


def compare_protected(spec,primary,si):
    if not primary.get('completed') or not si.get('independent_SI_network'):raise ValueError('incomplete protected trace')
    if primary['time_s']!=si['time_s'] or primary['sample_side']!=si['sample_side']:raise ValueError('protected trace clocks differ')
    if primary.get('settings')!=spec.coupled.model_dump(mode='json'):raise ValueError('protected settings mismatch')
    event_times=[t for t,side in zip(primary['time_s'],primary['sample_side']) if side=='pre_event']
    if len(event_times)!=len(set(event_times)):raise ValueError('multiple boundaries at one timestamp')
    def bound_events(r):return [e for e in r['event_log'] if e['kind'].startswith('controller_limit_')]
    left,right=bound_events(primary),bound_events(si)
    if [(e['kind'],e['equipment_id'],e['controller'],e['bound']) for e in left]!=[(e['kind'],e['equipment_id'],e['controller'],e['bound']) for e in right]:raise ValueError('independent controller boundary sequence differs')
    boundary_error=max((abs(a['time_s']-b['time_s']) for a,b in zip(left,right)),default=0.)
    result=full_compare(spec,primary,si)
    contracts=[physical_contract(spec,r) for r in (primary,si)]
    identities={}
    for p in spec.coupled.overexcitation_limiters:
        ch=primary['channels'];key=p.generator_id+'.OEL.'
        reaction=np.asarray(ch[key+'field_reaction_pu'])
        closed=np.asarray(ch[p.generator_id+'.connected'])==1
        identities[p.generator_id]=float(max(abs(reaction[closed]-reaction_from_trace(spec,primary,p.generator_id)[closed]),default=0.))
        measured=np.asarray(ch[key+'measured_current_pu']);bias=np.asarray(ch[key+'bias_pu'])
        if not np.isfinite(measured).all() or min(measured)<-1e-8 or min(bias)<0 or max(bias)>p.maximum_voltage_bias_pu:raise ValueError('physical OEL bounds violated')
    return dict(passed=boundary_error<=LIMITS['event_time_s'] and result['passed'] and all(c['passed'] for c in contracts) and all(v<=LIMITS['rotor_identity_pu'] for v in identities.values()),comparison=result,physical=contracts,reaction_identity_error=identities,maximum_controller_boundary_time_error_s=boundary_error)


def protocol():
    return dict(schema='power-grid-env.generator-excitation/v1',cases=list(NAMES),negative=list(NEGATIVE),native_cases=list(NATIVE),native_steps_s=list(STEPS),limits=LIMITS,
                code_sha256=package_fingerprint(),source_sha256=source_identity(),
                case_sha256={n:canonical_hash(case(n)) for n in NAMES},baseline_sha256={n:canonical_hash(baseline(n)) for n in NATIVE},
                native_OEL_feedback_runs=0,native_quantity='stock GENROU.XadIfd; explicitly OEL-free baselines')


def run(out,batch_size=2,native_batch_size=1):
    if batch_size<1 or native_batch_size<1:raise ValueError('positive batch sizes required')
    out.mkdir(parents=True,exist_ok=True);p=protocol()
    if (out/'protocol.json').exists():
        if json.loads((out/'protocol.json').read_text())!=p:raise ValueError('immutable protocol changed')
    else:write_json(out/'protocol.json',p)
    previous=json.loads((out/'summary.json').read_text()) if (out/'summary.json').exists() else {}
    for name,h in previous.get('artifacts_sha256',{}).items():
        if sha256_file(out/name)!=h:raise ValueError('changed cached raw artifact: '+name)
    write_json(out/'summary.json',dict(passed=False,status='in_progress'))
    done=[];native_done=[];new=0;new_native=0
    for name in NAMES:
        if protocol()!=p:raise ValueError('producer changed during corpus')
        folder=out/name;s=case(name);cache=(folder/'production.json').exists() and (folder/'SI.json').exists()
        if not cache:
            if new>=batch_size:continue
            print('protected',name,flush=True)
            write_json(folder/'study.json',s.model_dump(mode='json'))
            r=run_study(s,isolate=True,wall_timeout_s=90.)
            write_json(folder/'production.json',r)
            if not r.get('completed'):raise ValueError('primary did not complete '+name)
            si=independent_circuit(s).run(r);write_json(folder/'SI.json',si);new+=1
        else:r=json.loads((folder/'production.json').read_text());si=json.loads((folder/'SI.json').read_text())
        check=compare_protected(s,r,si);write_json(folder/'comparison.json',check)
        if not check['passed']:raise ValueError('protected comparison failed '+name)
        done.append(name)
    for name in NATIVE:
        if protocol()!=p:raise ValueError('producer changed during corpus')
        folder=out/'native-field'/name;s=baseline(name)
        cache=all((folder/f'andes-{h}.json').exists() for h in STEPS) and (folder/'production.json').exists()
        if not cache:
            if new_native>=native_batch_size:continue
            print('native field',name,flush=True);write_json(folder/'study.json',s.model_dump(mode='json'))
            # Native initialization is independent and happens before the production baseline.
            for h in STEPS:
                if not (folder/f'andes-{h}.json').exists():
                    r=native_reference(s,h,timeout_s=300.);write_json(folder/f'andes-{h}.json',r)
                    if not r.get('completed'):raise ValueError('native failed '+name)
            r=run_study(s,isolate=True,wall_timeout_s=90.);write_json(folder/'production.json',r)
            if not r.get('completed'):raise ValueError('baseline primary failed '+name)
            new_native+=1
        else:r=json.loads((folder/'production.json').read_text())
        checks=[compare_native(s,r,json.loads((folder/f'andes-{h}.json').read_text())) for h in STEPS]
        converged=all(checks[-1]['errors'][g]<=checks[0]['errors'][g]+LIMITS['convergence_floor_pu'] for g in checks[0]['errors'])
        check=dict(passed=all(c['passed'] for c in checks) and converged,refinements=checks,step_refinement_not_worse=converged)
        write_json(folder/'comparison.json',check)
        if not check['passed']:raise ValueError('native field comparison failed '+name)
        native_done.append(name)
    rejected=[]
    if len(done)==len(NAMES) and len(native_done)==len(NATIVE):
        for name in NEGATIVE:
            s=negative(name);r=run_study(s,isolate=True,wall_timeout_s=90.)
            write_json(out/'negative'/name/'study.json',s.model_dump(mode='json'));write_json(out/'negative'/name/'production.json',r)
            if r.get('completed'):raise ValueError('invalid OEL input accepted')
            rejected.append(name)
    if protocol()!=p:raise ValueError('producer changed during corpus')
    complete=len(done)==len(NAMES) and len(native_done)==len(NATIVE) and len(rejected)==len(NEGATIVE)
    artifacts={str(f.relative_to(out)):sha256_file(f) for f in sorted(out.rglob('*.json')) if f!=out/'summary.json'}
    result=dict(passed=complete,status='complete' if complete else 'partial',protected_cases=done,native_baseline_cases=native_done,rejected=rejected,
                native_field_runs=3*len(native_done),native_OEL_feedback_runs=0,code_sha256=p['code_sha256'],artifacts_sha256=artifacts)
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);p.add_argument('--batch-size',type=int,default=2);p.add_argument('--native-batch-size',type=int,default=1)
    a=p.parse_args();r=run(a.out,a.batch_size,a.native_batch_size);print({k:v for k,v in r.items() if k!='artifacts_sha256'})
