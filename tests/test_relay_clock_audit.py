import copy
import math
from types import SimpleNamespace
import pytest
from scipy.integrate import quad
from power_grid_env.relay_clock_audit import audit_clock,interval_integral,rate


def relay(curve='standard_inverse'):
    return SimpleNamespace(id='r',pickup=100.,curve=curve,time_multiplier_s=.1,reset_s=.05)


def trace(curve='standard_inverse'):
    r=relay(curve);k,a={'standard_inverse':(.14,.02),'very_inverse':(13.5,1),'extremely_inverse':(80,2)}[curve]
    trip=.1*k/(10**a-1)
    return r,dict(time_s=[0,trip/2,trip,trip,trip+.1],event_log=[dict(kind='network_relay_trip',equipment_id='r',time_s=trip)],channels={
        'r.measurement':[1000.]*5,'r.active':[1.]*5,'r.latched':[0.,0.,0.,1.,1.],
        'r.progress':[0.,.5,1.,1.,1.],'r.measurement_valid':[1.]*5})

@pytest.mark.parametrize('curve',['standard_inverse','very_inverse','extremely_inverse'])
def test_exact_constant_current_trip(curve):
    r,t=trace(curve);a=audit_clock(t,r)
    assert a['passed'] and a['max_progress_error']<1e-12

@pytest.mark.parametrize('curve',['standard_inverse','very_inverse','extremely_inverse'])
@pytest.mark.parametrize('left,right',[(50.,150.),(150.,50.),(1000.,1000.1),(100.,2000.)])
def test_independent_quadrature(curve,left,right):
    k,a={'standard_inverse':(.14,.02),'very_inverse':(13.5,1),'extremely_inverse':(80,2)}[curve]
    expected=quad(lambda x:max(((left+(right-left)*x)/100)**a-1,0)/(.1*k),0,1,epsabs=1e-12)[0]
    # Gaussian approximation of SI over a wide ramp has bounded interpolation
    # quadrature error; native studies use much shorter event-sided intervals.
    assert interval_integral(left,right,1,100,curve,.1)==pytest.approx(expected,rel=.015,abs=1e-12)


def test_exact_reset():
    r=relay('definite');r.time_multiplier_s=.2
    t=dict(time_s=[0,.1,.1,.2,.3],event_log=[],channels={
        'r.measurement':[200,200,0,0,0],'r.active':[1,1,0,0,0],'r.latched':[0]*5,
        'r.measurement_valid':[1]*5,'r.progress':[0,.5,.5,.5*math.exp(-2),.5*math.exp(-4)]})
    assert audit_clock(t,r)['passed']

@pytest.mark.parametrize('defect',['progress','late_trip','missing_event','fake_latch','unsided_change','nonfinite','length','time_reverse'])
def test_corruption(defect):
    r,t=trace()
    if defect=='progress':t['channels']['r.progress'][1]=.1
    if defect=='late_trip':t['event_log'][0]['time_s']+=.1
    if defect=='missing_event':t['event_log']=[]
    if defect=='fake_latch':t['channels']['r.latched'][1]=1
    if defect=='unsided_change':t['channels']['r.active'][1]=0
    if defect=='nonfinite':t['channels']['r.measurement'][1]=float('nan')
    if defect=='length':t['channels']['r.progress'].pop()
    if defect=='time_reverse':t['time_s'][1]=-1
    try:a=audit_clock(t,r)
    except ValueError:return
    assert not a['passed']


def test_nontripping_below_pickup():
    r=relay();t=dict(time_s=[0,1],event_log=[],channels={'r.measurement':[99,99],'r.active':[0,0],'r.latched':[0,0],'r.measurement_valid':[1,1],'r.progress':[0,0]})
    assert audit_clock(t,r)['passed']
    assert rate(99,100,'standard_inverse',.1)==0
