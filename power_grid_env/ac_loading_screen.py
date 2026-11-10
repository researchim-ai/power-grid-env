"""Reproducible bounded loading investigation; sampled turns are not certificates."""
from dataclasses import replace
from pathlib import Path
from pydantic import BaseModel,ConfigDict,Field,model_validator
from power_grid_env.ac_pseudo_arc import PseudoArcOptions
from power_grid_env.ac_envelope import run_envelope,verify_envelope
from power_grid_env.balanced_ac import case_document,case_from_document
from power_grid_env.evidence import package_fingerprint
from power_grid_env.study_artifacts import encoded,read_json,seal,verify


class LoadingScreen(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    case:dict
    starting_load_factor:float=Field(gt=0,le=3)
    requested_load_factor:float=Field(gt=0,le=3)
    options:PseudoArcOptions
    monitor:dict=Field(default_factory=dict)
    provenance:dict=Field(default_factory=dict)

    @model_validator(mode='after')
    def policy(self):
        if self.requested_load_factor<=self.starting_load_factor:raise ValueError('increasing load factors required')
        if self.options.dispatch is None or not self.options.enforce_q_limits:raise ValueError('explicit distributed P and projected Q controls required')
        return self


def study_input(document):
    d=LoadingScreen.model_validate(document);case=case_from_document(d.case)
    def scaled(factor):return case_document(replace(case,buses=[replace(b,pd=b.pd*factor,qd=b.qd*factor) for b in case.buses]))
    return dict(start=scaled(d.starting_load_factor),target=scaled(d.requested_load_factor),options=d.options.model_dump(mode='json'),monitor=d.monitor,provenance=dict(source=d.provenance,policy='all Pd and Qd scaled including signed loads; unchanged network, generator schedules and bounds'))


def assessment(document,study):
    d=LoadingScreen.model_validate(document);trace=study['trace'];env=study['envelope'];span=d.requested_load_factor-d.starting_load_factor
    turns=[]
    for turn in trace['turns']:
        estimate=turn['refinement']['estimate']
        if estimate is not None:turns.append(dict(lambda_value=estimate['lambda_value'],load_factor=d.starting_load_factor+span*estimate['lambda_value'],certified=False))
    reason='requested_target_reached' if trace['completed'] else 'sampled_turn_before_target' if trace['reason']=='sampled_turn_followed' and turns else 'branch_stopped_before_target'
    return dict(schema='power-grid-env.ac-loading-screen/v1',producer_sha256=package_fingerprint(),completed=bool(env['completed'] and trace['reason'] in ['target_reached','sampled_turn_followed']),reason=reason,trace_reason=trace['reason'],requested_target_reached=trace['completed'],starting_load_factor=d.starting_load_factor,requested_load_factor=d.requested_load_factor,maximum_sampled_load_factor=max((d.starting_load_factor+span*p['lambda_value'] for p in trace['points']),default=None),sampled_turns=turns,accepted_points=len(trace['points']),starting_operating_limits_satisfied=(not env['base_violations']) if trace['points'] else None,target_within_monitored_limits=env['target_within_monitored_limits'],global_target_absence_proven=False,stability_proven=False,certified_loading_margin=False,scope='finite sampled balanced AC branch with declared distributed P/Q policy; no dynamic stability or exhaustive branch search')


def run_loading_screen(document,out,*,cancelled=None):
    native=study_input(document);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    (out/'input.json').write_bytes(encoded(document));study=run_envelope(native,out/'study',cancelled=cancelled);result=assessment(document,study)
    (out/'result.json').write_bytes(encoded(result));seal(out);return result


def verify_loading_screen(out):
    out=Path(out);identity=verify(out);d=read_json(out/'input.json');verify_envelope(out/'study')
    if encoded(read_json(out/'study/input.json'))!=encoded(study_input(d)):raise ValueError('loading study input differs')
    study=dict(trace=read_json(out/'study/trace.json'),envelope=read_json(out/'study/envelope.json'));expected=assessment(d,study)
    if encoded(read_json(out/'result.json'))!=encoded(expected):raise ValueError('loading assessment differs')
    return dict(verified=True,completed=expected['completed'],requested_target_reached=expected['requested_target_reached'],manifest_sha256=identity,global_target_absence_proven=False)
