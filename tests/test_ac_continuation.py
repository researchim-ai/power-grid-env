from dataclasses import replace
import copy
import numpy as np
import pytest
from tests.test_balanced_ac_workflows import exact_case
from power_grid_env.ac_continuation import continue_power_flow,point,run_continuation,verify_continuation,physical_audit,ContinuationOptions
from power_grid_env.balanced_ac import solve_power_flow,case_document
from power_grid_env.study_artifacts import read_json,encoded,seal,export_zip,extract_zip


def cases():
    a,_=exact_case();b=replace(a,name='load-target',buses=[replace(bus,pd=bus.pd*1.1,qd=bus.qd*1.1) for bus in a.buses]);return a,b


def test_exact_target_reached_with_fresh_seed_and_no_input_mutation():
    a,b=cases();before=(copy.deepcopy(a),copy.deepcopy(b));r=continue_power_flow(a,b)
    assert r['completed'] and r['accepted_progress']==1 and r['target_loads_exact']
    assert (a,b)==before and r['attempts'][1]['progress']==pytest.approx(.1)
    assert all(t['audit']['balance_acceptable'] for t in r['attempts'])
    assert r['final_result']['vm_pu']==pytest.approx(solve_power_flow(b)['vm_pu'],abs=1e-9)


def test_rejected_step_rolls_back_to_last_accepted_voltage():
    a,b=cases();previous=None;accepted=0.;failed=False
    def solver(c,progress):
        nonlocal previous,accepted,failed
        if previous is not None:assert [bus.vm for bus in c.buses[1:]]==previous['vm_pu'][1:]
        if progress>.2 and not failed:
            failed=True;return dict(completed=False,status='newton_not_converged')
        r=solve_power_flow(c,enforce_q_limits=False,linear_solver='sparse');previous=r;accepted=progress;return r
    r=continue_power_flow(a,b,solver=solver)
    assert r['completed'] and failed
    bad=next(i for i,t in enumerate(r['attempts']) if not t['accepted'])
    assert r['attempts'][bad+1]['previous_accepted_progress']==r['attempts'][bad]['previous_accepted_progress']
    assert r['attempts'][bad+1]['progress']<r['attempts'][bad]['progress']


@pytest.mark.parametrize('kind',['topology','schedule','setpoint'])
def test_only_load_change_allowed(kind):
    a,b=cases()
    if kind=='topology':b=replace(b,branches=[replace(b.branches[0],x=.9),*b.branches[1:]])
    elif kind=='schedule':b=replace(b,generators=[replace(b.generators[0],pg=2.)])
    else:b=replace(b,buses=[replace(b.buses[0],vm=1.05),*b.buses[1:]])
    with pytest.raises(ValueError):continue_power_flow(a,b)


def test_pv_and_slack_setpoints_are_not_overwritten_by_previous_pq_voltage():
    a,b=cases();buses=[replace(bus,type_code=2) if i==1 else bus for i,bus in enumerate(a.buses)]
    a=replace(a,buses=buses);b=replace(b,buses=buses)
    seed=dict(vm_pu=[.8,.7,.9],va_deg=[0.,-.1,-.2])
    r=point(a,b,.5,seed)
    assert r.buses[0].vm==a.buses[0].vm and r.buses[1].vm==a.buses[1].vm and r.buses[2].vm==.9


@pytest.mark.parametrize('options,reason',[(dict(maximum_attempts=1),'maximum_attempts'),(dict(maximum_wall_s=1e-12),'wall_deadline')])
def test_incomplete_budget_never_rounds_to_target(options,reason):
    a,b=cases();r=continue_power_flow(a,b,options)
    assert not r['completed'] and r['reason']==reason and r['accepted_progress']<1 and r['final_result'] is None


def test_cancellation_and_failed_base_are_explicit():
    a,b=cases();assert continue_power_flow(a,b,cancelled=lambda:True)['reason']=='cancelled'
    assert continue_power_flow(a,b,solver=lambda c,p:dict(completed=False,status='singular_jacobian'))['reason']=='base_not_converged'


def test_corrupted_solution_is_not_accepted_on_converged_flag():
    a,b=cases()
    def forged(c,p):
        r=solve_power_flow(c);r['vm_pu'][1]+=.1;return r
    r=continue_power_flow(a,b,solver=forged)
    assert not r['completed'] and r['reason']=='base_not_converged'


@pytest.fixture(scope='module')
def packet(tmp_path_factory):
    a,b=cases();out=tmp_path_factory.mktemp('continuation')/'study'
    assert run_continuation(dict(start=case_document(a),target=case_document(b)),out)['completed'];return out


def test_packet_recompute_and_zip(packet,tmp_path):
    result=verify_continuation(packet);assert result['completed']
    export_zip(packet,tmp_path/'x.zip');extract_zip(tmp_path/'x.zip',tmp_path/'extracted')
    assert verify_continuation(tmp_path/'extracted')==result


@pytest.mark.parametrize('kind',['state','anchor','step','claim'])
def test_resealed_tamper_rejected(packet,tmp_path,kind):
    import shutil
    out=tmp_path/'tampered';shutil.copytree(packet,out);r=read_json(out/'result.json')
    if kind=='state':r['attempts'][1]['result']['vm_pu'][1]+=.1
    elif kind=='anchor':r['attempts'][1]['previous_accepted_progress']=.1
    elif kind=='step':r['attempts'][1]['step']=.9
    else:r['voltage_collapse_proven']=True
    (out/'result.json').write_bytes(encoded(r));(out/'manifest.json').unlink();seal(out)
    with pytest.raises(ValueError):verify_continuation(out)


def test_cli_and_schema(packet,tmp_path,capsys):
    from power_grid_env.cli import main
    assert main(['verify-ac-continuation',str(packet)])==0
    assert main(['schemas','--out',str(tmp_path/'schema')])==0
    assert read_json(tmp_path/'schema/ac-continuation-options.schema.json')['title']=='ContinuationOptions'


def test_late_solver_result_is_discarded_and_cannot_reach_target():
    import power_grid_env.ac_continuation as module
    a,b=cases();clock=[0.]
    def solve(c,p):
        r=solve_power_flow(c);clock[0]=2.;return r
    patch=pytest.MonkeyPatch();patch.setattr(module.time,'monotonic',lambda:clock[0])
    try:r=continue_power_flow(a,b,dict(maximum_wall_s=1),solver=solve)
    finally:patch.undo()
    assert r['reason']=='wall_deadline' and not r['completed']
    assert not r['attempts'][0]['accepted'] and r['attempts'][0]['discard_reason']=='wall_deadline'


def test_reference_callback_cannot_change_fixed_pg_to_fake_balance():
    from power_grid_env.network_opf import GeneratorSpec
    a,b=cases();g=GeneratorSpec(a.buses[1].id,-1000,1000,-1000,1000,(0.,),pg=0.,qg=0.)
    c=replace(a,generators=[*a.generators,g]);r=solve_power_flow(c,enforce_q_limits=False)
    # Compensating demand and generation would pass a balance-only audit.
    modified=replace(c,buses=[c.buses[0],replace(c.buses[1],pd=c.buses[1].pd+1),*c.buses[2:]])
    r['pg_mw'][1]+=1
    audit=physical_audit(modified,r,ContinuationOptions())
    assert audit['maximum_mismatch_pu']<1e-8 and not audit['schedules_acceptable'] and not audit['balance_acceptable']


def test_cli_run_is_concise_and_keeps_provenance(tmp_path,capsys):
    from power_grid_env.cli import main
    a,b=cases();p=tmp_path/'input.json';p.write_bytes(encoded(dict(start=case_document(a),target=case_document(b),provenance={'source':'Authored exact circuit'})))
    out=tmp_path/'out';assert main(['ac-continuation','--input',str(p),'--out',str(out)])==0
    text=capsys.readouterr().out;assert len(text)<2000 and 'attempt_count' in text
    assert read_json(out/'input.json')['provenance']['source']=='Authored exact circuit'
    assert verify_continuation(out)['completed']


def test_incomplete_packet_remains_incomplete_after_verification(tmp_path):
    a,b=cases();out=tmp_path/'partial'
    r=run_continuation(dict(start=case_document(a),target=case_document(b),options=dict(maximum_attempts=1)),out)
    assert not r['completed'] and not verify_continuation(out)['completed']


def test_continuation_retains_pv_q_limit_policy():
    from power_grid_env.network_opf import GeneratorSpec
    a,b=cases();a=replace(a,buses=[a.buses[0],replace(a.buses[1],type_code=2,vm=1.1),a.buses[2]],generators=[*a.generators,GeneratorSpec(20,-.01,.01,-500,500,(0.,),pg=0.,qg=0.)])
    b=replace(b,buses=[replace(x,pd=y.pd,qd=y.qd) for x,y in zip(a.buses,b.buses)],generators=a.generators)
    r=continue_power_flow(a,b,dict(enforce_q_limits=True))
    assert r['completed'] and abs(r['final_result']['qg_mvar'][1])==pytest.approx(.01,abs=1e-7)
    assert r['final_result']['vm_pu'][1]<1.1


def test_frozen_public_input_provenance_hashes():
    import hashlib
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'cases/ac-continuation/reference-inputs'
    origin=read_json(root/'origin.json')
    assert origin['shared_dc_seed']
    for name,sha in origin['files'].items():assert hashlib.sha256((root/name).read_bytes()).hexdigest()==sha
