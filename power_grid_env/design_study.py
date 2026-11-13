"""Bounded feeder selection against owner-authored mandatory motor studies.

Catalog impedances are whole-branch positive-sequence ohms, not cable ratings.
This first design profile does not certify ampacity, faults or selectivity.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from power_grid_env.industrial_dol import IndustrialDOLCase
from power_grid_env.motor_engineering import MotorEngineeringPlan,run_engineering,verify_engineering
from power_grid_env.study_artifacts import digest, encoded, seal, verify, read_json


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class FeederOption(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    resistance_ohm: float = Field(gt=0)
    reactance_ohm: float = Field(ge=0)
    provenance: str = Field(min_length=1, max_length=4096)

    @model_validator(mode='after')
    def source(self):
        if not self.provenance.strip():
            raise ValueError('nonblank catalog provenance required')
        return self


class FeederDesignTask(Contract):
    schema_version: Literal['power-grid-env.feeder-design/v1'] = 'power-grid-env.feeder-design/v1'
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    line_id: str = Field(min_length=1, max_length=128)
    options: list[FeederOption] = Field(min_length=1, max_length=32)
    mandatory_cases: list[IndustrialDOLCase] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def coverage(self):
        if len({o.id for o in self.options}) != len(self.options):
            raise ValueError('duplicate catalog option')
        if len({c.id for c in self.mandatory_cases}) != len(self.mandatory_cases):
            raise ValueError('duplicate mandatory case')
        baseline = None
        for case in self.mandatory_cases:
            if case.expectation != 'within_limits':
                raise ValueError('design requirements cannot accept expected violations')
            settings = case.study.induction if case.study.study == 'motor_start' else case.study.coupled
            if settings.duration_s > 10 or len(settings.motors) > 8 or settings.duration_s / settings.step_s > 4000:
                raise ValueError('bounded design profile: eight motors, ten seconds, 4000 intervals')
            network = case.study.network.model_dump(mode='json')
            if baseline is not None and network != baseline:
                raise ValueError('mandatory scenarios must share the same network')
            baseline = network
            lines = [line for line in case.study.network.lines if line.id == self.line_id]
            if len(lines) != 1 or len(lines[0].phases) != 3:
                raise ValueError('one three-phase design feeder required')
        # Reject unsupported physics or inadequate refinement coverage before
        # creating an output directory, not halfway through a design decision.
        MotorEngineeringPlan(id=self.id,cases=self.mandatory_cases)
        return self


class FeederProposal(Contract):
    option_id: str = Field(min_length=1, max_length=64)


def compile_design(task, proposal):
    """Candidates cannot edit requirements, disturbances or other equipment."""
    task = FeederDesignTask.model_validate(task.model_dump(mode='json') if isinstance(task, FeederDesignTask) else task)
    proposal = FeederProposal.model_validate(proposal)
    option = next((o for o in task.options if o.id == proposal.option_id), None)
    if option is None:
        raise ValueError('proposal must select an owner-supplied catalog option')
    cases = []
    for original in task.mandatory_cases:
        payload = original.model_dump(mode='json')
        line = next(line for line in payload['study']['network']['lines'] if line['id'] == task.line_id)
        # Diagonal balanced representation, replacing TOTAL branch impedance.
        for key, value in [('r_ohm', option.resistance_ohm), ('x_ohm', option.reactance_ohm)]:
            line[key] = [[value if i == j else 0. for j in range(3)] for i in range(3)]
        payload['parameter_provenance'] += '\nDesign feeder: ' + option.provenance
        settings=payload['study']['coupled'] if payload['study']['study']=='coupled_rms' else payload['study']['induction']
        # Preserve the owner's output grid as a subset. Resolve at least eight
        # samples per electrical period before the three-level thermal audit.
        for _ in range(16):
            if settings['step_s']<=1/(8*payload['study']['network']['frequency_hz']):break
            settings['step_s']/=2
        else:raise ValueError('design audit base grid exceeds bounded refinement')
        cases.append(IndustrialDOLCase.model_validate(payload))
    pack = MotorEngineeringPlan(id=task.id,cases=cases,per_run_timeout_s=60,total_timeout_s=600)
    return task, proposal, pack


def design_verdict(task,proposal,result):
    """An initialized, inaccurate or incomplete simulation is never approval."""
    complete=bool(result['completed'])
    qualified=bool(complete and all(c['numerically_qualified'] for c in result['cases']))
    accepted=bool(qualified and result['engineering_acceptable'])
    status='incomplete' if not complete else 'numerically_unqualified' if not qualified else 'accepted' if accepted else 'rejected'
    return dict(schema='power-grid-env.feeder-design-result/v2',
                task_sha256=digest(encoded(task.model_dump(mode='json'))),
                proposal_sha256=digest(encoded(proposal.model_dump(mode='json'))),
                option_id=proposal.option_id,completed=complete,status=status,
                numerically_qualified=qualified,meets_declared_dynamic_requirements=accepted,
                reward=1. if accepted else 0. if qualified else None,
                refinement_factors=[1,2,4],base_grid_policy='halve owner step until <=1/(8*f); preserve owner grid, never relax criteria',cases=result['cases'],code_sha256=result['producer_sha256'],
                scope='three-grid sampled single-cage balanced RMS, shaft/I2t/thermal audit; no ampacity, fault duty, selectivity or normative approval',
                independent_physics_oracle=False,qualified_electrical_design=False)


def run_design(task, proposal, out,*,cancelled=None):
    """Run every mandatory case on three grids in isolated bounded workers."""
    from pathlib import Path
    task, proposal, pack = compile_design(task, proposal)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'task.json').write_bytes(encoded(task.model_dump(mode='json')))
    (out / 'proposal.json').write_bytes(encoded(proposal.model_dump(mode='json')))
    result = run_engineering(pack, out / 'studies',cancelled=cancelled)
    verdict = design_verdict(task,proposal,result)
    (out / 'result.json').write_bytes(encoded(verdict))
    seal(out)
    return verdict


def verify_design(root,*,expected_task=None,expected_proposal=None):
    """Recompute audits/consensus; optional owner anchors prevent brief edits.

    A self-contained manifest proves consistency, not the author's identity.
    Evaluation callers must supply their trusted task and submitted proposal.
    """
    from pathlib import Path
    root=Path(root);identity=verify(root)
    task,proposal,plan=compile_design(read_json(root/'task.json'),read_json(root/'proposal.json'))
    if expected_task is not None:
        owner=FeederDesignTask.model_validate(expected_task)
        if owner.model_dump(mode='json')!=task.model_dump(mode='json'):raise ValueError('owner task anchor differs')
    if expected_proposal is not None and FeederProposal.model_validate(expected_proposal)!=proposal:raise ValueError('submitted proposal anchor differs')
    if read_json(root/'studies'/'input.json')!=plan.model_dump(mode='json'):raise ValueError('design compilation or audit limits differ')
    verify_engineering(root/'studies')
    verdict=design_verdict(task,proposal,read_json(root/'studies'/'summary.json'))
    if read_json(root/'result.json')!=verdict:raise ValueError('design verdict differs from physical evidence')
    if verify(root)!=identity:raise ValueError('design evidence changed during verification')
    return dict(verified=True,manifest_sha256=identity,owner_task_anchored=expected_task is not None,
                submitted_proposal_anchored=expected_proposal is not None,**verdict)
