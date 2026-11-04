"""Fail-closed EX-02 qualification of the registered engineering profiles."""
from pathlib import Path
import json,math
import numpy as np
from scipy.optimize import linear_sum_assignment
from benchmarks.ex02_cases import NAMES,MODAL,example,modal
from benchmarks.esst1a_network import run
from benchmarks.generator_esst1a_joint_validation import compare
from benchmarks.mnlex2_guard_audit import audit
from benchmarks.native_online_validation import source_identity
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json
from power_grid_env.study_spec import run_study

EIGEN_LIMIT=1e-4

def compare_modes(primary,reference):
    if not primary.get('completed') or not reference.get('completed'):raise ValueError('completed modal results required')
    a=np.array([complex(m['real_per_s'],m['imag_rad_per_s']) for m in primary['modes']]);b=np.array([complex(*v) for v in reference['eigenvalues_per_s']])
    if not len(a) or len(a)!=len(b) or len(a)!=reference['state_count'] or not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('finite matching modal inventories required')
    i,j=linear_sum_assignment(abs(a[:,None]-b));errors=abs(a[i]-b[j])
    return dict(passed=bool(max(errors)<=EIGEN_LIMIT),maximum_eigenvalue_error_per_s=float(max(errors)),limit_per_s=EIGEN_LIMIT,state_count=len(a),assignment=[dict(primary_index=int(x),native_index=int(y),error_per_s=float(e)) for x,y,e in zip(i,j,errors)])

def scenario_checks(name,primary,native):
    """Require the advertised physical behavior, not just two equal traces."""
    c=primary['channels'];checks={}
    if name=='overload_recovery':
        key='g2.ESST1A.oel_active'
        def transitions(r):
            v=r['channels'][key]
            return [(r['time_s'][i],v[i]) for i in range(1,len(v)) if v[i]!=v[i-1]]
        a,b=transitions(primary),transitions(native)
        checks['OEL_entry_and_release']=len(a)==len(b)==2 and [v for _,v in a]==[1.,0.] and [v for _,v in b]==[1.,0.] and all(abs(x[0]-y[0])<=1e-6 for x,y in zip(a,b))
    if name=='field_trip':checks['physical_field_trip']=min(c['g2.field_voltage_enabled'])==0
    if name=='pss_limited':checks['PSS_amplitude_limit_reached']=max(abs(np.asarray(c['g2.IEEEST.limited_pu'])))>=.0002-1e-12
    if name=='pss_voltage_gate':
        gate=np.asarray(c['g2.IEEEST.voltage_gate']);output=np.asarray(c['g2.IEEEST.output_pu'])
        checks['PSS_voltage_gate_entered_and_disabled_output_zero']=min(gate)==0 and max(gate)==1 and bool(np.all(output[gate==0]==0))
    if name=='authority_limited':checks['finite_authority_exercised']=min(c['g2.TakeoverOEL.ceiling_pu'])<=1.35+1e-8 and max(c['g2.TakeoverOEL.field_current_pu'])>1.2
    if name=='takeover_priority':
        checks['conflicting_floor_and_cap_exercised']=any(floor>cap and abs(field-cap)<1e-10 for floor,cap,field in zip(c['g2.ESST1A.uel_field_floor_pu'],c['g2.ESST1A.effective_oel_ceiling_pu'],c['g2.ESST1A.takeover_selected_pu']))
    return dict(passed=all(checks.values()),checks={k:bool(v) for k,v in checks.items()})


def qualify(out,root,names=NAMES,modal_names=MODAL,tolerances=(1e-13,1e-14)):
    if len(set(names))!=len(names) or not set(names)<=set(NAMES) or len(set(modal_names))!=len(modal_names) or not set(modal_names)<=set(MODAL):raise ValueError('unique registered cases required')
    if not names and not modal_names:raise ValueError('nonempty qualification required')
    if len(set(tolerances))<2 or any(not math.isfinite(t) or not 0<t<=1e-6 for t in tolerances):raise ValueError('two unique positive finite native tolerances required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False);code=package_fingerprint();source=source_identity()
    write_json(out/'protocol.json',dict(code_sha256=code,source_sha256=source,cases={n:example(n).model_dump(mode='json') for n in names},modal_cases={n:modal(n).model_dump(mode='json') for n in modal_names},native_tolerances=list(tolerances),eigenvalue_limit_per_s=EIGEN_LIMIT,event_limit_s=1e-6,state_limit_pu=2e-5,expected_failures_accepted=False,primary_states_or_clocks_imported=False,profile='FieldCurrentPI_AbsoluteTakeover_v1; canonical IEEEST MODE1; pinned OpenIPSL ESST1A/MNLEX2'))
    write_json(out/'summary.json',dict(passed=False,status='running'));rows=[]
    for kind,items,factory in [('network',names,example),('modal',modal_names,modal)]:
        for name in items:
            print(kind,name,flush=True);s=factory(name);directory=out/(kind+'-'+name);directory.mkdir();a=run_study(s);write_json(directory/'primary.json',a)
            for tolerance in tolerances:
                target=directory/str(tolerance)
                try:
                    if not a.get('completed'):raise ValueError('primary failed: '+str(a.get('reason')))
                    native_spec=s.model_copy(deep=True);native_spec.study='coupled_rms'
                    b=run(native_spec,target,root,tolerance,native_uels=kind=='network',linearize=kind=='modal');write_json(target/'reference.json',b)
                    if not b.get('native_ESST1A_in_feedback') or b.get('native_takeover_OEL_in_feedback')!=bool(s.coupled.takeover_oels) or b.get('native_PSS_in_feedback')!=any(m.stabilizer is not None for m in s.coupled.machines):raise ValueError('native closed feedback inventory differs')
                    c=compare(s,a,b) if kind=='network' else compare_modes(a,b)
                    if kind=='network':
                        c['native_guard_audit']=audit(s,b);c['scenario_checks']=scenario_checks(name,a,b);c['passed']=c['passed'] and c['scenario_checks']['passed']
                    write_json(target/'comparison.json',c)
                except Exception as exc:
                    c=dict(passed=False,error=type(exc).__name__,message=str(exc));target.mkdir(parents=True,exist_ok=True);write_json(target/'failure.json',c)
                rows.append(dict(kind=kind,case=name,tolerance=tolerance,**c));print(' ',tolerance,c['passed'],c.get('message',''),flush=True)
                write_json(out/'progress.json',dict(runs=rows))
    if code!=package_fingerprint() or source!=source_identity():raise ValueError('EX02 producer changed during qualification')
    result=dict(passed=all(r['passed'] for r in rows),status='complete',full_registered_corpus=set(names)==set(NAMES) and set(modal_names)==set(MODAL),code_sha256=code,source_sha256=source,runs=rows,industrial_profile_qualified=False)
    write_json(out/'summary.json',result);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=qualify(a.out,a.root);print(json.dumps(dict(passed=r['passed'],runs=len(r['runs']))));raise SystemExit(0 if r['passed'] else 1)
