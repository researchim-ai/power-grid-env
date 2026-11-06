"""45-s meshed network -> three islands -> checked reconnection, independent SI.

All switching permissions use independently calculated phasor derivatives.
No production stamps, RHS, operating point or event clocks enter the oracle.
"""
import math
import numpy as np
from scipy.optimize import brentq
from scipy.integrate import solve_ivp
from power_grid_env.build import NetBuilder
from power_grid_env.induction import InductionMotor
from power_grid_env.rms_study import ClassicalMachine
from power_grid_env.coupled_rms import CoupledSettings,CoupledEvent
from power_grid_env.network_automation import NetworkAutomation
from power_grid_env.study_spec import StudySpec

LINES=('feed_a','feed_b','tie')
MECHANICAL=[(10.,0,.0005),(12.,1,-.0003),(16.,0,-.0005),(17.,1,.0003),(27.,0,.0002),(28.,0,-.0002)]
COMMANDS=[(5.,k,False) for k in LINES]+[(15.,'tie',True)]+[(20.,k,True) for k in ('feed_a','feed_b')]+[(25.,k,False) for k in ('feed_a','feed_b')]+[(30.,k,True) for k in ('feed_a','feed_b')]


def mechanical_events(variant):
    if variant not in {'matched','mismatch'}:raise ValueError('registered island variant required')
    if variant=='matched':return [(10.,0,.001),(11.,0,-.001),(12.,1,-.0006),(13.,1,.0006),(27.,0,.0005),(28.,0,-.0005)]
    return [(t,i,value*(40 if variant=='mismatch' and t in {10.,16.} else 1)) for t,i,value in MECHANICAL]


def case(mode='radau',variant='matched'):
    if mode not in ('radau','network_dae'):raise ValueError('registered island integration method required')
    b=NetBuilder('long-islands','Authored symmetric mesh; two generator islands and live external-source island',10.,s_base_va=1e6)
    for node in ('grid','a','b'):b.node(node)
    b.source('grid',2.,8.)
    for id_,left,right in [('feed_a','grid','a'),('feed_b','grid','b'),('tie','a','b')]:b.line_z1(left,right,.2+1j,line_id=id_)
    motors=[];machines=[]
    for node in ('a','b'):
        b.load(node,300000.,100000.,model='constant_z',load_id='load_'+node)
        b.generator(node,300000.,mode='pv',v_set_pu=1.,q_min_var=-1e6,q_max_var=1e6,generator_id='g_'+node)
        motors.append(InductionMotor(id='m_'+node,node_id=node,rated_va=1e5,rated_ll_v=10000.,stator_r_pu=.02,stator_x_pu=.1,rotor_r_pu=.015,rotor_x_pu=.1,magnetizing_x_pu=3.,inertia_s=.5,fan_torque_pu=.6,connected=False))
        machines.append(ClassicalMachine(generator_id='g_'+node,rated_va=1e6,inertia_s=5.,damping_pu=5.,x_d_prime_pu=.25))
    p=CoupledSettings(duration_s=45.,step_s=.05,rtol=1e-10,atol=1e-12,motors=motors,machines=machines,event_policy='coincident_roots_v1',
        integration_method=mode,jacobian_format='csc',jacobian_network='direct_solve',network_automation=NetworkAutomation(breakers=[dict(id=k+'_cb',target_kind='line',target_id=k,opening_delay_s=.01,closing_delay_s=.01) for k in LINES]),
        events=[CoupledEvent(time_s=t,kind='breaker_close' if closed else 'breaker_open',equipment_id=k+'_cb') for t,k,closed in COMMANDS])
    p.events += [CoupledEvent(time_s=t,kind='generator_mechanical_step',equipment_id='g_'+('a' if i==0 else 'b'),value=value) for t,i,value in mechanical_events(variant)]
    return StudySpec(id='long-islands-'+variant+'-'+mode,study='coupled_rms',backend='scipy',network=b.build(),coupled=p)


def reference(variant='matched'):
    vb=10000/math.sqrt(3);zs=2+8j;zl=.2+1j;zg=25j;yl=(300000-100000j)/10000**2
    def initial(theta):
        plant=vb*np.exp(1j*theta);grid=(vb/zs+2*plant/zl)/(1/zs+2/zl);ig=yl*plant+(plant-grid)/zl
        return plant,ig
    theta=brentq(lambda a:3*(initial(a)[0]*np.conj(initial(a)[1])).real-300000,-.2,.2,xtol=1e-14)
    plant,ig=initial(theta);emf=plant+zg*ig;pm=np.full(2,3*(emf*np.conj(ig)).real/1e6)
    x=np.array([np.angle(emf),1.,np.angle(emf),1.]);closed={k:True for k in LINES}
    def physical(state):
        y=np.diag(np.array([1/zs,yl+1/zg,yl+1/zg],complex));drive=np.r_[vb/zs,abs(emf)*np.exp(1j*state[[0,2]])/zg]
        for k,(a,b) in zip(LINES,[(0,1),(0,2),(1,2)]):
            if closed[k]:y[a,a]+=1/zl;y[b,b]+=1/zl;y[a,b]-=1/zl;y[b,a]-=1/zl
        voltage=np.linalg.solve(y,drive);eg=abs(emf)*np.exp(1j*state[[0,2]]);current=(eg-voltage[1:])/zg
        pe=3*(eg*np.conj(current)).real/1e6
        rhs=np.ravel(np.column_stack([2*math.pi*50*(state[[1,3]]-1),(pm-pe-5*(state[[1,3]]-1))/10]))
        edot=1j*eg*rhs[[0,2]];dv=np.linalg.solve(y,np.r_[0j,edot/zg])
        return voltage,current,pe,rhs,dv
    actions={}
    for t,k,on in COMMANDS:actions.setdefault(t+.01,[]).append((k,on))
    mechanical={}
    for t,i,value in mechanical_events(variant):mechanical.setdefault(t,[]).append((i,value))
    boundaries=sorted(set(actions)|set(mechanical))
    axis=sorted(set([*np.arange(901)*.05,*boundaries]));times=[];channels={};log=[];maximum_kcl=0.
    def record(t):
        v,i,pe,_,_=physical(x);row={}
        for j,node in enumerate(('grid','a','b')):row[node+'.voltage_real_v']=v[j].real;row[node+'.voltage_imag_v']=v[j].imag
        for j,node in enumerate(('a','b')):
            power=3*v[j+1]*np.conj(i[j]);row.update({'g_'+node+'.delta':x[2*j],'g_'+node+'.omega':x[2*j+1],'g_'+node+'.p_w':power.real,'g_'+node+'.q_var':power.imag,'g_'+node+'.current_pu':abs(i[j])/(1e6/(math.sqrt(3)*10000))})
        for k,(a,b) in zip(LINES,[(0,1),(0,2),(1,2)]):row[k+'.from_current_a']=abs((v[a]-v[b])/zl) if closed[k] else 0.;row[k+'.closed']=float(closed[k])
        times.append(float(t))
        for key,value in row.items():channels.setdefault(key,[]).append(float(value))
    record(0.)
    for left,right in zip([0.,*boundaries],[*boundaries,45.]):
        grid=[float(t) for t in axis if left<t<=right]
        sol=solve_ivp(lambda t,state:physical(state)[3],(left,right),x,method='DOP853',rtol=2e-12,atol=1e-13,max_step=.02,dense_output=True)
        if not sol.success:raise ValueError('independent island integration failed')
        for t in grid:
            x=sol.sol(t)
            if t==right and right in mechanical:
                for index,value in mechanical[right]:
                    pm[index]+=value
                    log.append(dict(time_s=right,kind='generator_mechanical_step',equipment_id='g_'+('a' if index==0 else 'b'),outcome='applied'))
            if t==right and right in actions:
                for k,on in sorted(actions[right]):
                    permission=True;check=None
                    if closed[k]==on:
                        log.append(dict(time_s=right,kind='breaker_close' if on else 'breaker_open',equipment_id=k+'_cb',outcome='already_in_requested_state',independent_synchronism=None))
                        continue
                    if on:
                        v,_,_,_,dv=physical(x);a,b={'feed_a':(0,1),'feed_b':(0,2),'tie':(1,2)}[k]
                        hz=50+np.imag(dv/v)/(2*math.pi)
                        angle=abs(np.angle(v[a]*np.conj(v[b])));voltage=abs(abs(v[a])-abs(v[b]))/vb;frequency=abs(hz[a]-hz[b])
                        permission=bool(min(abs(v[a]),abs(v[b]))/vb>=.1 and angle<=math.pi/18 and voltage<=.1 and frequency<=.1)
                        check=dict(angle_rad=float(angle),voltage_difference_pu=float(voltage),frequency_difference_hz=float(frequency))
                    if permission:closed[k]=on
                    log.append(dict(time_s=right,kind='breaker_close' if on else 'breaker_open',equipment_id=k+'_cb',outcome='applied' if permission else 'blocked',independent_synchronism=check))
            record(t)
        x=sol.y[:,-1].copy()
    return dict(completed=True,time_s=times,channels=channels,event_log=log,independent_SI=True,production_stamps_used=False,production_initialization_used=False,
                maximum_simultaneous_islands=3,scope='two identical loaded GENCLS islands plus external-source island; constant-Z loads; no induction starts while islanded')


def compare(primary,native):
    from power_grid_env.topology import bus_identifier
    if not primary.get('completed') or not native.get('completed'):raise ValueError('complete island studies required')
    expected=[(e['kind'],e['equipment_id'],e['outcome']) for e in native['event_log']]
    actual=[e for e in primary['event_log'] if e['kind'] in {'breaker_open','breaker_close','generator_mechanical_step'} and e.get('outcome') in {'applied','blocked','failed_to_operate','already_in_requested_state'}]
    if [(e['kind'],e['equipment_id'],e['outcome']) for e in actual]!=expected:raise ValueError('island permission/action order differs')
    if max(abs(a['time_s']-b['time_s']) for a,b in zip(actual,native['event_log']))>1e-8:raise ValueError('island breaker deadline differs')
    axis=np.array(primary['time_s']);indices=[]
    for t in native['time_s']:
        found=np.flatnonzero(abs(axis-t)<=1e-10)
        if not len(found):raise ValueError('missing island sample')
        indices.append(int(found[-1]))
    errors={};limits={}
    for key,wanted in native['channels'].items():
        if '.voltage_' in key:
            node,part=key.split('.');bus=bus_identifier([node]);v=np.array(primary['channels'][bus+'.v'])[indices]*np.exp(1j*np.array(primary['channels'][bus+'.a'])[indices])*10000/math.sqrt(3)
            measured=v.real if part=='voltage_real_v' else v.imag;limit=.01
        else:
            measured=np.array(primary['channels'][key])[indices];limit=0. if key.endswith('.closed') else 2e-6 if key.endswith('.delta') else 2e-8 if key.endswith('.omega') else .1 if key.endswith(('_w','_var')) else 1e-5
        errors[key]=float(max(abs(measured-np.array(wanted))));limits[key]=limit
    return dict(passed=all(e<=limits[k] for k,e in errors.items()),errors=errors,limits=limits,all_actions_independently_checked=True,maximum_islands=3)
