"""Qualification of explicit active OEL2C / AVR equilibrium coordination."""
from pathlib import Path
import argparse,json
from benchmarks.generator_oel2c_active_cases import NAMES,example
from benchmarks.generator_oel2c_loaded_validation import run as validate

NEGATIVE=('missing_reference_policy','passive_reference_policy','no_loaded_point','subthermal_current','wrong_control_current','cold_timer','zero_downward_ramp','inverse_zero_ramp','pretripped_field','unknown','duplicate','field_base')


def negative(name):
    d=example('hold').model_dump(mode='json');s=d['coupled'];p=s['oel2c_limiters'][0]
    if name=='missing_reference_policy':p['avr_reference_initialization']='unmodified'
    elif name=='passive_reference_policy':p['initialization']='inactive_equilibrium'
    elif name=='no_loaded_point':s['operating_point']=None
    elif name=='subthermal_current':p['rated_field_reaction_pu']=2.
    elif name=='wrong_control_current':p['KAct']=.1
    elif name=='cold_timer':p['VInvMaxPu']=-1.
    elif name=='zero_downward_ramp':p['Krd']=0.
    elif name=='inverse_zero_ramp':p['Sw1']=True;p['K1']=0.
    elif name=='pretripped_field':s['generator_field_thermal']=[dict(generator_id='g2',rated_field_reaction_pu=.8,time_constant_s=.3,trip_level=1.1,initialization='equilibrium')]
    elif name=='unknown':p['generator_id']='missing'
    elif name=='duplicate':s['oel2c_limiters']*=2
    elif name=='field_base':s['generator_field_thermal']=[dict(generator_id='g2',rated_field_reaction_pu=1.,time_constant_s=.3,trip_level=3.)]
    else:raise ValueError('unknown active OEL2C negative input')
    return d


def run(out,root,names=NAMES):
    return validate(out,root,names,case_factory=example,negative_factory=negative,registered_names=NAMES,negative_names=NEGATIVE,profile='active_limited_equilibrium_with_explicit_AVR_reference_policy')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));a=p.parse_args();print(json.dumps(run(a.out,a.root,tuple(a.cases)),indent=2))
