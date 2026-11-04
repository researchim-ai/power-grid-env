"""Preregistered current-regulating network and native interior PID validation."""
from pathlib import Path
import argparse,json
from benchmarks.generator_oel2c_regulating_cases import example,NAMES
from benchmarks.generator_oel2c_loaded_validation import run as validate

NEGATIVE=('legacy_model','missing_reference','missing_voltage_bounds','wrong_policy','cold_target','cold_timer','unavailable_authority','positive_output','voltage_bounds','reactive_limit','unstable_controller','no_loaded_point')


def negative(name):
    d=example('hold').model_dump(mode='json');s=d['coupled'];p=s['oel2c_limiters'][0]
    if name=='legacy_model':p['model']='OEL2C_Dynawo_cap_guard_v1_SEXS_summation'
    elif name=='missing_reference':p['specified_avr_reference_pu']=None
    elif name=='missing_voltage_bounds':p['regulating_voltage_bounds_pu']=None
    elif name=='wrong_policy':p['avr_reference_initialization']='derive_from_operating_point_with_OEL'
    elif name=='cold_target':p['ILimPu']=.8
    elif name=='cold_timer':p['VInvMaxPu']=-1.
    elif name=='unavailable_authority':p['specified_avr_reference_pu']=1.5
    elif name=='positive_output':p['specified_avr_reference_pu']=.9
    elif name=='voltage_bounds':p['regulating_voltage_bounds_pu']=[.99,1.05]
    elif name=='reactive_limit':d['network']['generators'][1]['q_min_var']=-1.;d['network']['generators'][1]['q_max_var']=1.
    elif name=='unstable_controller':p['KpOel']=.3;p['KiOel']=2.;p['KdOel']=.01
    elif name=='no_loaded_point':s['operating_point']=None
    else:raise ValueError('unknown regulating negative input')
    return d


def run(out,root,names=NAMES):
    return validate(out,root,names,case_factory=example,negative_factory=negative,registered_names=NAMES,negative_names=NEGATIVE,profile='current_regulating_equilibrium_with_specified_AVR')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--cases',nargs='+',choices=NAMES,default=list(NAMES));a=p.parse_args();print(json.dumps(run(a.out,a.root,tuple(a.cases)),indent=2))
