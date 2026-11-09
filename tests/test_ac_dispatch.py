from dataclasses import replace
import copy,json
import numpy as np
import pytest
from power_grid_env.ac_dispatch import DispatchSystem,DispatchOptions,solve_dispatch,run_dispatch,verify_dispatch,audit_dispatch
from power_grid_env.balanced_ac import case_document,solve_power_flow
from tests.test_ac_reactive_arc import controlled
from power_grid_env.study_artifacts import read_json,seal,export_zip,extract_zip,encoded


def network():
    a,_=controlled(count=2)
    return replace(a,buses=[replace(b,vm=1.04) if b.id==0 else replace(b,pd=.3) for b in a.buses],
        generators=[replace(a.generators[0],pg=.2,pmin=0.,pmax=1.,qmin=-.03,qmax=.03),*[replace(g,pg=.1,qmin=-1.,qmax=1.) for g in a.generators[1:]]])


def opts(**kw):return dict(participants=[dict(generator_index=0,weight=1.),dict(generator_index=1,weight=2.),dict(generator_index=2,weight=1.)],**kw)


@pytest.mark.parametrize('side',['upper','lower'])
def test_former_slack_hits_q_limit_without_losing_phase_reference(side):
    c=network()
    if side=='lower':c=replace(c,buses=[replace(c.buses[0],vm=.96),*c.buses[1:]])
    r=solve_dispatch(c,opts())
    assert r['completed'],r
    s=r['result'];assert s['q_modes']['0']==side
    assert s['vm_pu'][0]<1.04 if side=='upper' else s['vm_pu'][0]>.96
    assert s['qg_mvar'][0]==pytest.approx(.03 if side=='upper' else -.03,abs=1e-9)
    assert s['va_deg'][0]==0 and s['pg_mw']==pytest.approx([.25,.2,.15],abs=1e-9)
    assert audit_dispatch(c,DispatchOptions.model_validate(opts()),s)['balance_acceptable']


@pytest.mark.parametrize('mode',['upper','lower'])
def test_active_limits_redistribute_to_remaining_participants(mode):
    c=network()
    if mode=='upper':c=replace(c,generators=[replace(c.generators[0],pmax=.22),*c.generators[1:]])
    else:c=replace(c,generators=[replace(c.generators[0],pg=.4,pmin=.38),*[replace(g,pg=.3) for g in c.generators[1:]]])
    r=solve_dispatch(c,opts());assert r['completed'],r
    s=r['result'];assert s['p_modes'][0]==mode
    assert s['pg_mw'][0]==pytest.approx(.22 if mode=='upper' else .38,abs=1e-10)
    assert sum(s['pg_mw'])==pytest.approx(.6,abs=1e-9)
    if mode=='upper':assert s['pg_mw'][1]-.1==pytest.approx(2*(s['pg_mw'][2]-.1),abs=1e-9)


def test_lossy_network_loss_is_shared_not_dropped():
    c=network();c=replace(c,branches=[replace(b,r=.02) for b in c.branches]);r=solve_dispatch(c,opts());assert r['completed'],r
    s=r['result'];assert sum(s['pg_mw'])>sum(b.pd for b in c.buses)
    assert s['pg_mw'][1]-.1==pytest.approx(2*(s['pg_mw'][0]-.2),abs=1e-9)


@pytest.mark.parametrize('qmax,expected',[(.03,'upper'),(2.,'voltage')])
def test_reactive_limit_release_from_separate_operating_conditions(qmax,expected):
    c=network();c=replace(c,generators=[replace(c.generators[0],qmin=-2.,qmax=qmax),*c.generators[1:]])
    r=solve_dispatch(c,opts());assert r['completed'] and r['result']['q_modes']['0']==expected


def test_reanchoring_and_equipment_order_do_not_change_physical_solution():
    c=network();r=solve_dispatch(c,opts());other=solve_dispatch(c,opts(angle_reference_bus=2))
    assert r['completed'] and other['completed']
    assert other['result']['vm_pu']==pytest.approx(r['result']['vm_pu'],abs=1e-9)
    assert np.array(other['result']['va_deg'])-other['result']['va_deg'][0]==pytest.approx(r['result']['va_deg'],abs=1e-8)
    cc=replace(c,buses=c.buses[::-1],generators=c.generators[::-1]);oo=opts();oo['participants']=[dict(generator_index=2-p['generator_index'],weight=p['weight']) for p in oo['participants']]
    perm=solve_dispatch(cc,oo);assert perm['completed']
    assert perm['result']['pg_mw'][::-1]==pytest.approx(r['result']['pg_mw'],abs=1e-9)


@pytest.mark.parametrize('clamp',[False,True])
def test_augmented_analytic_jacobian_matches_finite_differences(clamp):
    c=network()
    if clamp:c=replace(c,generators=[replace(c.generators[0],pmax=.22),*c.generators[1:]])
    o=DispatchOptions.model_validate(opts());f=DispatchSystem(c,o);r=solve_dispatch(c,opts());assert r['completed']
    x=f.pack(r['result'],r['result']['delta_mw']/c.base_mva);_,j=f.evaluate(x,True)
    for i in range(len(x)):
        d=np.zeros(len(x));d[i]=1e-6
        assert (f.evaluate(x+d)-f.evaluate(x-d))/2e-6==pytest.approx(j.toarray()[:,i],abs=2e-8)


def test_capacity_exhaustion_is_not_a_convergence_or_global_absence_claim():
    c=network();c=replace(c,generators=[replace(g,pmax=g.pg+.001) for g in c.generators]);r=solve_dispatch(c,opts())
    assert not r['completed'] and r['reason']=='participating_p_limits_active'
    assert r['result'] is None and not r['target_absence_proven']


def test_artifact_cli_schema_and_zip(tmp_path,capsys):
    from power_grid_env.cli import main
    from power_grid_env.schema_export import export_schemas
    d=dict(case=case_document(network()),options=opts());p=tmp_path/'input.json';p.write_bytes(encoded(d))
    assert main(['ac-dispatch','--input',str(p),'--out',str(tmp_path/'packet')])==0
    assert json.loads(capsys.readouterr().out)['completed']
    assert main(['verify-ac-dispatch',str(tmp_path/'packet')])==0
    assert json.loads(capsys.readouterr().out)['verified']
    export_zip(tmp_path/'packet',tmp_path/'packet.zip');extract_zip(tmp_path/'packet.zip',tmp_path/'restored')
    assert verify_dispatch(tmp_path/'restored')==verify_dispatch(tmp_path/'packet')
    export_schemas(tmp_path/'schemas');assert (tmp_path/'schemas/ac-dispatch-options.schema.json').exists()


@pytest.mark.parametrize('kind',['pg','q','mode','reference','delta','claim'])
def test_resealed_result_tampering_rejected(tmp_path,kind):
    p=tmp_path/'packet';run_dispatch(dict(case=case_document(network()),options=opts()),p);r=read_json(p/'result.json')
    if kind=='pg':r['result']['pg_mw'][0]+=.01
    elif kind=='q':r['result']['qg_mvar'][0]+=.01
    elif kind=='mode':r['result']['q_modes']['0']='voltage'
    elif kind=='reference':r['result']['angle_reference_bus']=2
    elif kind=='delta':r['result']['delta_mw']+=.01
    else:r['frequency_dynamics']=True
    (p/'result.json').write_bytes(encoded(r));(p/'manifest.json').unlink();seal(p)
    with pytest.raises(ValueError):verify_dispatch(p)


@pytest.mark.parametrize('options',[opts(maximum_iterations=1),opts(maximum_wall_s=1e-12)])
def test_partial_budget_outcomes_are_verifiable(tmp_path,options):
    r=run_dispatch(dict(case=case_document(network()),options=options),tmp_path/'packet')
    assert not r['completed'] and verify_dispatch(tmp_path/'packet')['verified']


def test_cancel_before_seed_and_invalid_participants(monkeypatch):
    import power_grid_env.ac_dispatch as m
    monkeypatch.setattr(m,'solve_power_flow',lambda *a,**k:(_ for _ in ()).throw(AssertionError('seed executed')))
    assert solve_dispatch(network(),opts(),cancelled=lambda:True)['reason']=='cancelled'
    for options in [dict(participants=[dict(generator_index=99,weight=1)]),dict(participants=[dict(generator_index=0,weight=1)]*2),opts(angle_reference_bus=99)]:
        with pytest.raises(ValueError):solve_dispatch(network(),options)


def test_original_pandapower_distributed_slack_matches_unlimited_profile():
    import pandapower as pp
    c=network();c=replace(c,generators=[replace(g,qmin=-10.,qmax=10.) for g in c.generators],branches=[replace(b,r=.02) for b in c.branches])
    r=solve_dispatch(c,opts());assert r['completed']
    net=pp.create_empty_network(sn_mva=1.)
    for b in c.buses:
        pp.create_bus(net,vn_kv=1.,index=b.id);pp.create_load(net,b.id,p_mw=b.pd,q_mvar=b.qd)
    for i,g in enumerate(c.generators):pp.create_gen(net,g.bus,p_mw=g.pg,vm_pu=c.buses[g.bus].vm,slack=g.bus==0,slack_weight=[.25,.5,.25][i])
    for b in c.branches:pp.create_impedance(net,b.fbus,b.tbus,rft_pu=b.r,xft_pu=b.x,sn_mva=1.)
    pp.runpp(net,distributed_slack=True,tolerance_mva=1e-10,calculate_voltage_angles=True,lightsim2grid=False)
    assert r['result']['pg_mw']==pytest.approx(net.res_gen.p_mw.tolist(),abs=1e-8)
    assert r['result']['vm_pu']==pytest.approx(net.res_bus.vm_pu.tolist(),abs=1e-8)


def test_independent_numerical_jacobian_reference_with_limits():
    from benchmarks.ac_dispatch_reference import solve_reference
    c=network();c=replace(c,generators=[replace(c.generators[0],pmax=.22),*c.generators[1:]])
    r=solve_dispatch(c,opts());ref=solve_reference(c,opts())
    assert r['completed'] and ref['maximum_residual_pu']<1e-9
    assert r['result']['pg_mw']==pytest.approx(ref['result']['pg_mw'],abs=1e-8)
    assert r['result']['vm_pu']==pytest.approx(ref['result']['voltage_pu'],abs=1e-8)


def test_phase_reference_can_be_a_pq_bus_and_islands_are_not_inferred(tmp_path):
    c=network();c=replace(c,buses=[c.buses[0],c.buses[1],replace(c.buses[2],type_code=1)])
    r=solve_dispatch(c,opts(angle_reference_bus=2));assert r['completed'] and r['result']['va_deg'][2]==0
    c=replace(c,branches=[replace(b,status=0) for b in c.branches])
    d=dict(case=case_document(c),options=opts());r=run_dispatch(d,tmp_path/'packet')
    assert not r['completed'] and r['reason']=='islanded' and verify_dispatch(tmp_path/'packet')['verified']


def test_multiple_units_share_q_bound_and_zero_range_p_unit_is_not_a_reserve():
    c=network();g=c.generators[0]
    c=replace(c,generators=[replace(g,pg=.1,pmin=.1,pmax=.1,qmin=-.015,qmax=.015),replace(g,pg=.1,qmin=-.015,qmax=.015),*c.generators[1:]])
    o=dict(participants=[dict(generator_index=i,weight=1.) for i in range(4)])
    r=solve_dispatch(c,o);assert r['completed']
    assert r['result']['pg_mw'][0]==pytest.approx(.1,abs=1e-9)
    assert r['result']['qg_mvar'][:2]==pytest.approx([.015,.015],abs=1e-9)


def test_single_bus_balance_and_nonparticipant_schedule():
    c=network();c=replace(c,buses=[replace(c.buses[0],pd=.5,qd=.01)],branches=[],generators=[c.generators[0]])
    r=solve_dispatch(c,dict(participants=[dict(generator_index=0,weight=1.)]));assert r['completed']
    assert r['result']['pg_mw']==pytest.approx([.5],abs=1e-9)
    c=network();r=solve_dispatch(c,dict(participants=[dict(generator_index=1,weight=1.)]));assert r['completed']
    assert r['result']['pg_mw']==pytest.approx([.2,.3,.1],abs=1e-9)


def test_reference_never_calls_primary_equations_or_jacobian(monkeypatch):
    from benchmarks.ac_dispatch_reference import solve_reference
    import power_grid_env.balanced_sparse as sparse
    def forbidden(*args,**kw):raise AssertionError('primary electrical implementation called')
    monkeypatch.setattr(sparse,'admittance',forbidden);monkeypatch.setattr(sparse,'derivatives',forbidden)
    monkeypatch.setattr(DispatchSystem,'evaluate',forbidden)
    assert solve_reference(network(),opts())['maximum_residual_pu']<1e-9
