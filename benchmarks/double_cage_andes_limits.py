"""Analytic Motor5 circuit equivalence, declared limits and a literal-label audit.

Identical physical cages collapse to Motor3. Native Motor5 is checked with a
closed-form transfer-preserving parameter mapping and a small-R1 limit; ordinary matching parameter labels are audited,
not asserted to be a general physical parallel-cage equivalence.
"""
from pathlib import Path
import argparse
import numpy as np
from benchmarks.andes_motor_reference import native_reference,STEPS,LIMITS
from power_grid_env.study_spec import StudySpec,run_study
from power_grid_env.report import write_json,sha256_file
from power_grid_env.evidence import package_fingerprint


def comparison(spec,oracle,motor_model,steps,out):
    r=run_study(spec,isolate=True,wall_timeout_s=60.)
    if not r.get('completed'):raise ValueError('production model incomplete')
    write_json(out/'study.json',spec.model_dump(mode='json'));write_json(out/'native-study.json',oracle.model_dump(mode='json'));write_json(out/'production.json',r);rows=[]
    for h in steps:
        native=native_reference(oracle,h,motor_model=motor_model)
        if not native.get('completed'):raise ValueError('external motor failed: '+str(native))
        errors={key:float(max(abs(np.interp(r['time_s'],native['time_s'],values)-r['channels'][spec.induction.motors[0].id+'.'+key]))) for key,values in native['channels'].items()}
        rows.append(dict(step_s=h,errors=errors,within_limits=all(errors[k]<=limit for k,limit in LIMITS.items()),native_zero_slip_decay_per_s=native['native_zero_slip_rotor_decay_per_s']));write_json(out/f'native-{h}.json',native)
    result=dict(steps=rows,finest_limits=LIMITS,passed_limit=rows[-1]['within_limits'] and rows[-1]['errors']['speed_pu']<rows[0]['errors']['speed_pu'] if len(rows)>1 else rows[0]['within_limits'])
    write_json(out/'comparison.json',result);return result


def native5_coordinates(spec):
    """Closed-form transfer-preserving circuit reparameterization, not fitting."""
    m=spec.induction.motors[0];wb=2*np.pi*spec.network.frequency_hz;xm=m.magnetizing_x_pu
    matrix=np.array([[xm+m.rotor_x_pu,xm],[xm,xm+m.rotor2_x_pu]])+m.rotor_mutual_x_pu
    inverse=np.linalg.inv(matrix);resistance=np.diag([m.rotor_r_pu,m.rotor2_r_pu]);c=xm*inverse@np.ones(2)
    decay=wb*resistance@inverse;rates=np.sort(np.linalg.eigvals(decay).real)
    d=xm*sum(c);moment=wb*sum(np.diag(resistance)*c*c);a=(d*rates[1]-moment)/(rates[1]-rates[0]);b=d-a
    epsilon=64*np.finfo(float).eps*max(1.,d)
    if a<=epsilon or b<=epsilon or rates[1]-rates[0]<=epsilon:raise ValueError('unobservable/degenerate cage mode needs a separate collapsed-model limit')
    leakage=m.stator_x_pu+xm-d;g1=xm-a;g2=leakage-m.stator_x_pu
    oracle=spec.model_copy(deep=True);oracle.id=spec.id+'-native5-coordinates';p=oracle.induction.motors[0]
    p.rotor_x_pu=xm*g1/(xm-g1);p.rotor_r_pu=rates[0]*(p.rotor_x_pu+xm)/wb
    p.rotor2_x_pu=g1*g2/(g1-g2);p.rotor2_r_pu=rates[1]*(p.rotor2_x_pu+g1)/wb;p.rotor_mutual_x_pu=0.
    oracle=StudySpec.model_validate(oracle.model_dump(mode='json'))
    native_a=np.array([[-rates[0],0.],[rates[1]-rates[0],-rates[1]]]);native_b=1j*np.array([a*rates[0],a*rates[0]+b*rates[1]])
    errors=[]
    for point in [0.,.001,1.+3j,2.+50j,1j*wb,.1+2j*wb]:
        physical=1j*c@np.linalg.solve(point*np.eye(2)+decay,wb*resistance@inverse@(xm*np.ones(2)))
        native=np.linalg.solve(point*np.eye(2)-native_a,native_b)[1];errors.append(float(abs(physical-native)))
    if max(errors)>1e-10:raise ValueError('analytic transfer mapping residual')
    mapping=dict(method='Closed form from two rotor poles, DC EMF response and first input moment; no trajectory fitting',physical_decay_rates_per_s=rates.tolist(),effective_reactance_pu=float(leakage),DC_rotor_EMF_response_pu=float(d),first_input_moment_per_s=float(moment),maximum_transfer_residual_pu=max(errors),native_parameter_semantics='ANDES Motor5 circuit coordinates, not literal physical cage resistances/leakages',native_parameters={k:getattr(p,k) for k in ['rotor_r_pu','rotor_x_pu','rotor2_r_pu','rotor2_x_pu','rotor_mutual_x_pu']})
    return oracle,mapping


def check(out):
    out=Path(out);base=StudySpec.model_validate_json(Path('cases/studies/double_cage_cold.json').read_text());base.induction.duration_s=1.2
    equal=base.model_copy(deep=True);equal.id='double-cage-identical-external-limit';m=equal.induction.motors[0];m.rotor2_r_pu=m.rotor_r_pu;m.rotor2_x_pu=m.rotor_x_pu;m.rotor_mutual_x_pu=.03
    oracle=equal.model_copy(deep=True);m=oracle.induction.motors[0];m.rotor_r_pu/=2;m.rotor_x_pu=m.rotor_x_pu/2+m.rotor_mutual_x_pu;m.model='single_cage';m.rotor2_r_pu=None;m.rotor2_x_pu=None;m.rotor_mutual_x_pu=0.
    collapse=comparison(equal,oracle,'Motor3',STEPS,out/'identical-cages')
    slow=base.model_copy(deep=True);slow.id='double-cage-small-r1-external-limit';slow.induction.motors[0].rotor_r_pu=1e-8
    singular=comparison(slow,slow,'Motor5',STEPS,out/'small-first-resistance')
    ordinary=comparison(base,base,'Motor5',[STEPS[-1]],out/'ordinary-diagnostic')
    mapped_oracle,mapping=native5_coordinates(base);mapped=comparison(base,mapped_oracle,'Motor5',STEPS,out/'mapped-physical-cages')
    special=StudySpec.model_validate_json(Path('cases/studies/double_cage_frequency_base.json').read_text());special.induction.duration_s=1.2;special.induction.motors[0].rotor_mutual_x_pu=.04
    special_oracle,special_mapping=native5_coordinates(special);mapped_special=comparison(special,special_oracle,'Motor5',STEPS,out/'mapped-mutual-60Hz')
    write_json(out/'mapped-physical-cages'/'mapping.json',mapping);write_json(out/'mapped-mutual-60Hz'/'mapping.json',special_mapping)

    m=base.induction.motors[0];wb=2*np.pi*base.network.frequency_hz;matrix=np.array([[m.magnetizing_x_pu+m.rotor_x_pu,m.magnetizing_x_pu],[m.magnetizing_x_pu,m.magnetizing_x_pu+m.rotor2_x_pu]])
    physical=np.sort(np.linalg.eigvals(-wb*np.diag([m.rotor_r_pu,m.rotor2_r_pu])@np.linalg.inv(matrix)).real);native=np.sort(ordinary['steps'][0]['native_zero_slip_decay_per_s']);different=bool(max(abs(physical-native))>1e-3)
    result=dict(passed=bool(collapse['passed_limit'] and singular['passed_limit'] and mapped['passed_limit'] and mapped_special['passed_limit'] and different),identical_cages_Motor3_limit=collapse,small_first_resistance_Motor5_limit=singular,mapped_physical_cages_Motor5=mapped,mapped_mutual_60Hz_Motor5=mapped_special,analytic_parameter_mappings=[mapping,special_mapping],ordinary_Motor5_diagnostic=ordinary,ordinary_general_equivalence_claim=False,physical_zero_slip_decay_per_s=physical.tolist(),native_Motor5_zero_slip_decay_per_s=native.tolist(),ordinary_state_spectra_differ=different,scope='Two external limits and two analytically reparameterized Motor5 cases; literal labels are not equated. Constant linear windings, cold starts only; no trajectory fitting or timing shift.',code_sha256=package_fingerprint(),source_sha256=sha256_file(Path(__file__)),adapter_sha256=sha256_file(Path('benchmarks/andes_motor_reference.py')),field_validated=False,industrial_profile_qualified=False)
    write_json(out/'summary.json',result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);a=p.parse_args();r=check(a.out);print(r['passed'],r['physical_zero_slip_decay_per_s'],r['native_Motor5_zero_slip_decay_per_s'])
    if not r['passed']:raise SystemExit(1)


if __name__=='__main__':main()
