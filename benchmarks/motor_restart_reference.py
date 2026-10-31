"""Independent SI trajectory and decision check for the authored DOL timing matrix.

One common motor bus, one source/line, one deep voltage plateau. Expected
switching times come from inputs, never the production event log. This does not
validate arbitrary off-site contactors or continuous extrema.
"""
from pathlib import Path
import argparse,json
import numpy as np
from power_grid_env.study_spec import StudySpec
from power_grid_env.induction import InductionEvent
from power_grid_env.motor_restart import MotorRestartProtocol,run_motor_restart
from power_grid_env.evidence import package_fingerprint
from power_grid_env.report import write_json,sha256_file
from benchmarks.induction_reference import rotor_flux_group_reference

KEYS=['speed_pu','e_real_pu','e_imag_pu','current_pu','bus_voltage_pu','p_w','q_var']
LIMITS=dict(zip(KEYS,[2e-5,2e-5,2e-5,1e-4,1e-5,100.,100.]))


def check(out):
    out=Path(out);study_path=Path('cases/studies/motor_contactor_staggered_group.json');protocol_path=Path('cases/restart/direct-on-line-group.json')
    base=StudySpec.model_validate_json(study_path.read_text());protocol=MotorRestartProtocol.model_validate_json(protocol_path.read_text())
    actual=run_motor_restart(base,protocol,out/'experiment');rows=[];passing=[]
    if not actual['completed']:raise ValueError('finite experiment incomplete')
    for item in actual['cases']:
        folder=out/'experiment'/item['id'];spec=StudySpec.model_validate_json((folder/'study.json').read_text());report=json.loads((folder/'result.json').read_text());oracle=spec.model_copy(deep=True)
        # Authored source loss/recovery 0.2/0.5 s; well-separated thresholds.
        assert [(e.time_s,e.value) for e in spec.induction.events]==[(.2,.5),(.5,1.)]
        expected=[]
        for motor in oracle.induction.motors:
            p=motor.contactor;opened=.2+p.dropout_delay_s;closed=max(.5+p.pickup_delay_s,opened+p.minimum_off_time_s)
            expected.append(dict(time_s=opened,kind='motor_open',equipment_id=motor.id))
            if closed<spec.induction.duration_s:expected.append(dict(time_s=closed,kind='motor_close',equipment_id=motor.id))
            motor.contactor=None
        expected.sort(key=lambda e:e['time_s']);oracle.induction.events += [InductionEvent.model_validate(e) for e in expected];oracle.induction.events.sort(key=lambda e:e.time_s)
        got=[e for e in report['event_log'] if e.get('cause') in {'undervoltage_timer','automatic_restart'}]
        action_gate=len(got)==len(expected) and all((a['kind'],a['equipment_id'])==(b['kind'],b['equipment_id']) and abs(a['time_s']-b['time_s'])<1e-8 for a,b in zip(got,expected))
        write_json(out/'reference'/item['candidate_id']/'oracle-study.json',oracle.model_dump(mode='json'))
        reference=rotor_flux_group_reference(oracle,report);errors={m:{key:float(max(abs(np.array(report['channels'][m+'.'+key])-values[:,j]))) for j,key in enumerate(KEYS)} for m,values in reference.items()}
        checks={};connections={};times=report['time_s'];sides=report['sample_side']
        for observation in protocol.observations:
            m=observation.motor_id;values=reference[m];on=[]
            for t,side in zip(times,sides):
                connected=True
                for e in expected:
                    if e['equipment_id']!=m:continue
                    tied=abs(e['time_s']-t)<=64*np.finfo(float).eps*max(1.,abs(t))
                    if e['time_s']<t and not tied or tied and side!='pre_event':connected=e['kind']=='motor_close'
                on.append(connected)
            connections[m]=on
            r=observation.recovery;start=None;confirmed=None
            for i,t in enumerate(times):
                if i+1<len(times) and times[i+1]==t:continue
                if t<r.from_s:continue
                if not on[i] or values[i,0]<r.minimum_speed_pu:start=None
                elif start is None:start=t
                if start is not None and t-start>=r.dwell_s-1e-12:confirmed=t;break
            checks[m]=dict(final_speed=bool(values[-1,0]>=observation.minimum_final_speed_pu),bus_voltage=bool(min(values[:,4])>=observation.minimum_bus_voltage_pu),current=bool(max(values[:,3])<=observation.maximum_current_pu),connected=on[-1],recovery=confirmed is not None and confirmed-r.from_s<=r.maximum_delay_s+1e-12)
        for w in protocol.voltage_windows:
            assert all(m.node_id==w.node_id for m in base.induction.motors),'reference only covers the common receiving bus'
            values=reference[base.induction.motors[0].id][:,4]
            selected=[v for t,side,v in zip(times,sides,values) if w.from_s-1e-12<=t<=w.to_s+1e-12 and not(abs(t-w.from_s)<1e-12 and side=='pre_event')]
            checks['window:'+w.id]=dict(voltage=bool(min(selected)>=w.minimum_voltage_pu and max(selected)<=w.maximum_voltage_pu))
        decision=all(v for group in checks.values() for v in group.values())
        if decision:passing.append(item['candidate_id'])
        row=dict(candidate_id=item['candidate_id'],passed=bool(action_gate and decision==item['within_limits'] and all(v<=LIMITS[k] for group in errors.values() for k,v in group.items())),expected_actions=expected,action_gate=action_gate,SI_checks=checks,SI_within_limits=decision,production_within_limits=item['within_limits'],maximum_errors=errors,limits=LIMITS);rows.append(row)
        write_json(out/'reference'/item['candidate_id']/'si-reference.json',dict(time_s=times,sample_side=sides,channels={m:dict(zip(KEYS,v.T.tolist()))|{'connected':connections[m]} for m,v in reference.items()}));write_json(out/'reference'/item['candidate_id']/'comparison.json',row)
    result=dict(passed=all(r['passed'] for r in rows) and passing==actual['candidates_within_sampled_limits'],cases=rows,independent_candidates_within_limits=passing,code_sha256=package_fingerprint(),source_sha256=sha256_file(Path(__file__)),si_source_sha256=sha256_file(Path('benchmarks/induction_reference.py')),study_file_sha256=sha256_file(study_path),protocol_file_sha256=sha256_file(protocol_path),field_validated=False,industrial_profile_qualified=False,global_optimum_claim=False)
    write_json(out/'summary.json',result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);args=p.parse_args();r=check(args.out);print(r['passed'],r['independent_candidates_within_limits'])
    if not r['passed']:raise SystemExit(1)


if __name__=='__main__':main()
