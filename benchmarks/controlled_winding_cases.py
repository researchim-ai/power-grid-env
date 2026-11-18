"""Authored interior TGOV1/SEXS motor-heat profiles, not OEM passports."""
from itertools import product
from benchmarks.mixed_equilibrium_cases import example as base


def example(controls='both',fault=False,mode='equilibrium'):
    s=base(fault=fault);c=s['coupled'];c['winding_generator_profile']='GENCLS_TGOV1_SEXS';c['operating_point']['winding_state']=mode
    p=c['machines'][0]
    if controls in ['governor','both']:p['governor']=dict(R=.05,T1=.1,T2=.2,T3=1.,VMIN=0.,VMAX=1.2,Dt=.2)
    if controls in ['exciter','both']:p['exciter']=dict(TATB=.1,TB=.1,K=5.,TE=.1,EMIN=0.,EMAX=5.)
    if fault:c['faults'][0].update(r_ohm=.5,x_ohm=.2)
    return s


def jobs():
    for controls,fault,mode in product(['governor','exciter','both'],[False,True],['passport','equilibrium']):
        yield dict(id=controls+'-'+mode+'-'+('fault' if fault else 'hold'),study=example(controls,fault,mode),fault=fault)


def refusals():
    for kind in ['governor_limit','exciter_limit','missing_profile']:
        s=example()
        if kind=='governor_limit':s['coupled']['machines'][0]['governor']['VMAX']=.1
        elif kind=='exciter_limit':s['coupled']['machines'][0]['exciter']['EMAX']=1.
        else:s['coupled']['winding_generator_profile']='GENCLS_fixed_field'
        yield dict(id='negative-'+kind,study=s)
