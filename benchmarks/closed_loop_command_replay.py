"""Separate plant time-step effects from sampled feedback sensitivity.

A full physical run records the controller's voltage commands on the carrier
clock. Further runs replay that same command tape at three electrical step
sizes. This is a diagnostic experiment, never an independent physics oracle
or a replacement for the original closed-loop temporal qualification.
"""
import argparse
import hashlib
import numpy as np
from pathlib import Path
from benchmarks.extended_closed_loop_acceptance import document,producer
from benchmarks.extended_closed_loop_refinement import assessment
from benchmarks.extended_closed_loop_reference import channels
from benchmarks.extended_temporal_budget import arrays
from power_grid_env import switching_drive as runtime
from power_grid_env.study_artifacts import encoded,seal,read_json,verify

NAMES=('double_cage','saturated_four_core','warm_four_winding')
STEPS=(2.5e-6,1.25e-6,6.25e-7)


class Recorder:
    def __init__(self,controller,tape):self.controller=controller;self.tape=tape
    def update(self,t,current,speed,dc,period,enabled):
        u,diagnostics=self.controller.update(t,current,speed,dc,period,enabled)
        self.tape.append(dict(time_s=t,period_s=period,voltage_ab_v=[u.real,u.imag],control={k:v.item() if isinstance(v,np.generic) else v for k,v in diagnostics.items()}))
        return u,diagnostics
    def observe(self,*args):return self.controller.observe(*args)


class Tape:
    def __init__(self,tape):self.tape=tape;self.index=0
    def update(self,t,current,speed,dc,period,enabled):
        if self.index>=len(self.tape):raise ValueError('command tape exhausted')
        row=self.tape[self.index];self.index+=1
        if abs(t-row['time_s'])>5e-13 or period!=row['period_s']:raise ValueError('command clock differs')
        return complex(*row['voltage_ab_v']),dict(row['control'])
    def observe(self,*args):pass


def trajectory(p,tape,record,freeze_pwm=False):
    original=runtime.SwitchingDrive;original_pwm=runtime.pwm_plan;instances=[]
    class Experiment(original):
        def __init__(self,study):
            super().__init__(study);self.controller=Recorder(self.controller,tape) if record else Tape(tape);instances.append(self)
    def planned(u,dc,period,dead_time):
        if record:
            duty,edges=original_pwm(u,dc,period,dead_time)
            tape[-1].update(pwm_duty=duty.tolist(),pwm_edges_s=[float(x) for x in edges])
            return duty,edges
        if freeze_pwm:
            row=tape[instances[0].controller.index-1]
            return np.array(row['pwm_duty']),list(row['pwm_edges_s'])
        return original_pwm(u,dc,period,dead_time)
    runtime.SwitchingDrive=Experiment;runtime.pwm_plan=planned
    try:
        r=runtime.run_switching_drive(p)
        if not record and instances[0].controller.index!=len(tape):raise ValueError('command tape not fully consumed')
        return r
    finally:runtime.SwitchingDrive=original;runtime.pwm_plan=original_pwm


def run(out):
    out=Path(out);out.mkdir(exist_ok=False);before=dict(qualification=producer(),benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest());rows=[]
    (out/'producer.json').write_bytes(encoded(before))
    for name in NAMES:
        p=document(name);tape=[];seed=trajectory(dict(p,max_step_s=1.25e-6),tape,True)
        (out/(name+'-seed.json')).write_bytes(encoded(seed));(out/(name+'-commands.json')).write_bytes(encoded(tape));runs=[]
        reductions={};edge_counts={}
        for mode,freeze in [('voltage_command',False),('voltage_and_pwm',True)]:
            runs=[];counts=[]
            for index,step in enumerate(STEPS):
                result=trajectory(dict(p,max_step_s=step),tape,False,freeze);runs.append(arrays(p,result,'primary'));(out/f'{name}-{mode}-{index}.json').write_bytes(encoded(result))
                m=result['metrics'];counts.append(m['gate_edges'])
                if m['final_energy_relative_error']>=1e-7 or m['kcl_max_a']>=5e-5 or m['motor_constitutive_residual_v']>=1e-8:raise ValueError('frozen-command plant failed physical audits')
            reductions[mode]=assessment(p,*runs);edge_counts[mode]=counts
        row=dict(case=name,commands=len(tape),frozen_refinement=reductions,gate_edge_counts=edge_counts,fully_frozen_edge_counts_match=len(set(edge_counts['voltage_and_pwm']))==1,note='observer/control columns are tape metadata; voltage_command still rescales PWM from actual DC; voltage_and_pwm freezes duty and edge clock, while plant/source/DC/RC/shaft and enable/block logic remain physical')
        rows.append(row);print(encoded(row).decode(),flush=True)
    if producer()!=before['qualification'] or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=before['benchmark_sha256']:raise ValueError('command experiment producer changed')
    result=dict(schema='power-grid-env.frozen-command-diagnostic/v1',completed=True,rows=rows,trajectories=7*len(NAMES),steps_s=list(STEPS),command_source_step_s=1.25e-6,scope='same production solver, voltage-only and voltage+PWM tape experiments; enable/block/diodes/source/DC and thermal/shaft remain physical; no independent validation, no closed-loop pass replacement',superiority_established=False)
    (out/'summary.json').write_bytes(encoded(result));identity=seal(out);assert verify(out)==identity;print(encoded(dict(completed=True,manifest_sha256=identity)).decode(),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);run(parser.parse_args().out)
