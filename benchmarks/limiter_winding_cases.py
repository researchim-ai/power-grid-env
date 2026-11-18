"""Authored two-sided controller pulses with motor winding R(T)."""
from benchmarks.controlled_winding_cases import example as base
from itertools import product


def example(controls='both',mode='equilibrium',reverse=False):
    s=base(controls,False,mode);c=s['coupled'];c.update(duration_s=.5,events=[])
    p=c['machines'][0]
    if p.get('governor'):p['governor'].update(VMIN=.1,VMAX=.3)
    if p.get('exciter'):p['exciter'].update(EMIN=1.,EMAX=1.4)
    for t,sign in [(.05,1.),(.15,-1.),(.25,-1.),(.35,1.)]:
        for kind,size,parameter in [('governor_step',.6,'governor'),('voltage_reference_step',.3,'exciter')]:
            if p.get(parameter):c['events'].append(dict(kind=kind,time_s=t,equipment_id='g',value=sign*size))
    if reverse:c['events'].reverse()
    return s


def jobs():
    for controls,mode in product(['governor','exciter','both'],['passport','equilibrium']):
        yield dict(id=controls+'-'+mode,study=example(controls,mode),controls=controls,mode=mode,reverse=False)
    for mode in ['passport','equilibrium']:
        yield dict(id='both-'+mode+'-reversed',study=example('both',mode,True),controls='both',mode=mode,reverse=True)
