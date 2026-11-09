from dataclasses import replace
import copy
import numpy as np
import pytest
from power_grid_env.network_opf import Bus,Branch,GeneratorSpec,OpfCase
from power_grid_env.balanced_ac import case_document,solve_power_flow
from power_grid_env.ac_pseudo_arc import trace_power_flow,audit_trace,run_pseudo_arc,verify_pseudo_arc
from power_grid_env.ac_reactive_arc import ReactivePolarFamily


def controlled(q0=0.,q1=.6,count=1):
    buses=[Bus(0,3,0.,0.,0.,0.,1.,0.,.02,2.)]
    generators=[GeneratorSpec(0,-10.,10.,-100.,100.,(0.,),pg=0.,qg=0.)];branches=[]
    for i in range(1,count+1):
        buses.append(Bus(i,2,.2,q0,0.,0.,1.,0.,.02,2.))
        generators.append(GeneratorSpec(i,qmin=-.15,qmax=.15,pmin=0.,pmax=1.,cost=(0.,),pg=.1,qg=0.))
        branches.append(Branch(0,i,0.,.1,0.,100.,0.,0.,1,-180,180))
    a=OpfCase('reactive-control',1.,buses,generators,branches)
    return a,replace(a,buses=[buses[0]]+[replace(b,qd=q1) for b in buses[1:]])


def document(a,b,**opts):return dict(start=case_document(a),target=case_document(b),options=dict(enforce_q_limits=True,**opts))


@pytest.mark.parametrize('q0,q1,mode',[(0.,.6,'upper'),(0.,-.6,'lower'),(.6,0.,'voltage'),(-.6,0.,'voltage')])
def test_enters_and_releases_both_limits(q0,q1,mode):
    a,b=controlled(q0,q1);d=document(a,b);r=trace_power_flow(a,b,d['options'])
    assert r['completed'] and r['points'][-1]['control_modes']['1']==mode
    assert r['control_changes'] and all(e['refinement']['localized'] for e in r['control_changes'])
    assert audit_trace(d,r)['verified']
    final=r['final_result'];q=final['qg_mvar'][1];v=final['vm_pu'][1]
    assert -.15000001<=q<=.15000001
    if mode=='upper':assert q==pytest.approx(.15,abs=1e-9) and v<1
    if mode=='lower':assert q==pytest.approx(-.15,abs=1e-9) and v>1
    if mode=='voltage':assert v==pytest.approx(1.,abs=1e-10)


@pytest.mark.parametrize('q0,q1',[(0.,.6),(0.,-.6),(.6,0.),(-.6,0.)])
def test_original_pandapower_equations_independently_match_every_sample(q0,q1):
    import pandapower as pp
    a,b=controlled(q0,q1);r=trace_power_flow(a,b,dict(enforce_q_limits=True))
    assert r['completed']
    for p in r['points']:
        net=pp.create_empty_network(sn_mva=1.)
        s=pp.create_bus(net,vn_kv=1.);v=pp.create_bus(net,vn_kv=1.)
        pp.create_ext_grid(net,s,vm_pu=1.)
        pp.create_impedance(net,s,v,rft_pu=0.,xft_pu=.1,sn_mva=1.)
        pp.create_load(net,v,p_mw=.2,q_mvar=q0+(q1-q0)*p['lambda_value'])
        pp.create_gen(net,v,p_mw=.1,vm_pu=1.,min_q_mvar=-.15,max_q_mvar=.15)
        pp.runpp(net,enforce_q_lims=True,tolerance_mva=1e-10,init='flat',max_iteration=40,lightsim2grid=False)
        assert p['result']['vm_pu']==pytest.approx(net.res_bus.vm_pu.to_numpy(),abs=1e-8)
        assert p['result']['va_deg']==pytest.approx(net.res_bus.va_degree.to_numpy(),abs=1e-7)
        assert p['result']['qg_mvar'][1]==pytest.approx(net.res_gen.q_mvar.iloc[0],abs=1e-8)


@pytest.mark.parametrize('q0',[0.,.6,-.6])
def test_piecewise_analytic_jacobian_and_lambda_column(q0):
    a,b=controlled(q0,.3);f=ReactivePolarFamily(a,b)
    seed=solve_power_flow(a,enforce_q_limits=True,tolerance_pu=1e-11)
    u=f.pack(seed,0.);_,j=f.evaluate(u,True);g=f.g.copy()
    expected=np.column_stack([j.toarray(),g])
    for i in range(len(u)):
        d=np.zeros(len(u));d[i]=1e-6
        assert (f.evaluate(u+d)-f.evaluate(u-d))/2e-6==pytest.approx(expected[:,i],abs=2e-8)


def test_simultaneous_controllers_and_bus_order_invariance():
    a,b=controlled(count=2);r=trace_power_flow(a,b,dict(enforce_q_limits=True))
    aa=replace(a,buses=a.buses[::-1],generators=a.generators[::-1]);bb=replace(b,buses=b.buses[::-1],generators=b.generators[::-1])
    other=trace_power_flow(aa,bb,dict(enforce_q_limits=True))
    assert r['completed'] and other['completed']
    assert r['final_result']['vm_pu']==pytest.approx(other['final_result']['vm_pu'][::-1],abs=1e-10)
    assert len(r['control_changes'])==2
    assert r['control_changes'][0]['after_point']==r['control_changes'][1]['after_point']


@pytest.mark.parametrize('kind',['mode','inventory','wrong_voltage_side','q'])
def test_artifact_rejects_control_tampering(kind):
    a,b=controlled();d=document(a,b);r=trace_power_flow(a,b,d['options']);bad=copy.deepcopy(r)
    if kind=='mode':bad['points'][-1]['control_modes']['1']='voltage'
    elif kind=='inventory':bad['control_changes']=[]
    elif kind=='wrong_voltage_side':bad['points'][-1]['result']['vm_pu'][1]=1.1
    else:bad['points'][-1]['result']['qg_mvar'][1]=.2
    with pytest.raises(ValueError):audit_trace(d,bad)


def test_sealed_projected_control_packet_and_partial_budget(tmp_path):
    a,b=controlled();d=document(a,b);r=run_pseudo_arc(d,tmp_path/'packet')
    assert r['completed'] and verify_pseudo_arc(tmp_path/'packet')['verified']
    d=document(a,b,maximum_points=2);r=trace_power_flow(a,b,d['options'])
    assert not r['completed'] and r['reason']=='maximum_points'
    assert audit_trace(d,r)['verified']


@pytest.mark.parametrize('step',[.04,.02])
def test_localized_entry_matches_closed_form_reactive_limit(step):
    a,b=controlled();r=trace_power_flow(a,b,dict(enforce_q_limits=True,step=step,maximum_step=step))
    # Qgen = Qload + (1-cos(theta))/X, sin(theta)=-0.1*0.1.
    qline=(1-np.sqrt(1-.01**2))/.1
    exact=(.15-qline)/.6
    e=r['control_changes'][0]['refinement']
    assert e['localized']
    assert e['left']['lambda_value']-2e-9<=exact<=e['right']['lambda_value']+2e-9
    assert e['right']['lambda_value']-e['left']['lambda_value']<=1.1e-7


def test_direct_opposite_limits_are_split_and_both_changes_recorded():
    a,b=controlled(.6,-.6);d=document(a,b,step=.25,maximum_step=.5)
    r=trace_power_flow(a,b,d['options'])
    assert r['completed'] and audit_trace(d,r)['verified']
    assert [(e['before'],e['after']) for e in r['control_changes']]==[('upper','voltage'),('voltage','lower')]


@pytest.mark.parametrize('kind',['localized','endpoint','history','extra'])
def test_localization_certificate_tampering(kind):
    a,b=controlled();d=document(a,b);r=trace_power_flow(a,b,d['options'])
    point=next(p for p in r['points'] if p.get('control_brackets'))
    br=point['control_brackets']['1']
    # outcome references the same bracket objects; mutate both inventories.
    if kind=='localized':br['localized']=False
    elif kind=='endpoint':br['right']['result']['vm_pu'][1]+=.01
    elif kind=='history':br['history'][0]['arc_step']+=.001
    else:point['control_brackets']['99']=copy.deepcopy(br)
    with pytest.raises(ValueError):audit_trace(d,r)


def test_no_pv_profile_retains_closed_form_fold():
    from tests.test_ac_pseudo_arc import analytic
    a,b=analytic();d=document(a,b);r=trace_power_flow(a,b,d['options'])
    assert not r['completed'] and r['reason']=='sampled_turn_followed'
    assert r['turns'][0]['refinement']['estimate']['lambda_value']==pytest.approx(2/3,abs=1e-7)
    assert not r['control_changes'] and audit_trace(d,r)['verified']


def test_multiple_units_at_one_voltage_controller_use_aggregate_q_limits():
    a,b=controlled();g=a.generators[1]
    units=[a.generators[0],replace(g,pg=.05,qmin=-.075,qmax=.075),replace(g,pg=.05,qmin=-.075,qmax=.075)]
    a=replace(a,generators=units);b=replace(b,generators=units)
    d=document(a,b);r=trace_power_flow(a,b,d['options'])
    assert r['completed'] and audit_trace(d,r)['verified']
    assert r['final_result']['qg_mvar'][1:]==pytest.approx([.075,.075],abs=1e-9)


def test_independent_reference_releases_wrong_side_one_way_clamp_on_ieee118():
    from pathlib import Path
    from power_grid_env.study_artifacts import read_json
    from power_grid_env.balanced_ac import case_from_document
    from benchmarks.reactive_arc_reference import solve_reference,normalize_control_reference
    source=read_json(Path(__file__).resolve().parents[1]/'cases/ac-reactive-arc/reference-inputs/case118.json')
    a=case_from_document(source['case']);factor=1+.2*.3121184047553292
    b=replace(a,buses=[replace(x,pd=x.pd*factor,qd=x.qd*factor) for x in a.buses])
    f=ReactivePolarFamily(b,b)
    def residual(net,active):
        r=normalize_control_reference(case_document(b),net,active,source)
        return max(abs(f.evaluate(f.pack(dict(vm_pu=r['voltage_pu'],va_deg=np.rad2deg(r['angle_rad'])),0.))))
    net,active,_=solve_reference(source,factor,release=False)
    assert residual(net,active)>1e-5
    net,active,iterations=solve_reference(source,factor)
    assert iterations>1 and residual(net,active)<1e-9
