"""Owner-anchored radial LV sizing plus three-grid motor acceptance.

The transformer is referred to its secondary as a Thevenin impedance under a
stiff primary source. This profile does not model its magnetizing/inrush branch.
"""
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from power_grid_env.electrical_sizing import Contract, SizingTask, Selection, calculate, run_sizing, verify_sizing
from power_grid_env.design_study import FeederDesignTask, compile_design, run_design, verify_design
from power_grid_env.industrial_dol import IndustrialDOLCase
from power_grid_env.study_artifacts import digest, encoded, read_json, seal, verify


class EquipmentDesignTask(Contract):
    schema_version: Literal['power-grid-env.equipment-design/v1'] = 'power-grid-env.equipment-design/v1'
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    sizing: SizingTask
    line_id: str = Field(min_length=1,max_length=128)
    mandatory_cases: list[IndustrialDOLCase] = Field(min_length=1,max_length=8)

    @model_validator(mode='after')
    def radial_profile(self):
        baseline=None
        for case in self.mandatory_cases:
            study=case.study
            if study.study!='motor_start' or study.induction is None:
                raise ValueError('equipment profile requires motor_start studies')
            n=study.network
            if len(n.sources)!=1 or len(n.lines)!=1 or len(n.connectivity_nodes)!=2 or len(n.voltage_levels)!=1:
                raise ValueError('exactly one source, one feeder and two LV nodes required')
            if any((n.transformers,n.switches,n.generators,n.inverters,n.storages,n.shunts,n.neutral_grounds,n.study_faults,n.harmonics,n.omissions)):
                raise ValueError('extra network equipment unsupported by radial sizing profile')
            if n.lines[0].id!=self.line_id or n.voltage_levels[0].nominal_ll_v!=self.sizing.voltage_v or n.sources[0].ll_v!=self.sizing.voltage_v:
                raise ValueError('feeder identity or LV voltage differs from sizing task')
            terminals={t.id:t.connectivity_node_id for t in n.terminals}
            line=n.lines[0];source=n.sources[0]
            upstream=terminals[source.terminal_id]
            endpoints={terminals[line.terminal_from],terminals[line.terminal_to]}
            if len(endpoints)!=2 or upstream not in endpoints:
                raise ValueError('source and feeder connectivity differs from radial profile')
            downstream=next(node for node in endpoints if node!=upstream)
            settings=study.induction
            if any(m.node_id!=downstream or m.rated_ll_v!=self.sizing.voltage_v for m in settings.motors):
                raise ValueError('all motors must match LV voltage and be downstream of feeder')
            if any(terminals[l.terminal_id]!=downstream for l in n.loads):
                raise ValueError('all auxiliary loads must be downstream of feeder')
            if any(any(v<0 for v in list(l.p_w.values())+list(l.q_var.values())) for l in n.loads):
                raise ValueError('passive lagging auxiliary loads required')
            envelope=sum(m.rated_va for m in settings.motors)+sum(math.hypot(sum(l.p_w.values()),sum(l.q_var.values())) for l in n.loads)
            if self.sizing.demand_kva*1000 < envelope:
                raise ValueError('sizing demand must cover connected motor nameplate VA plus auxiliary apparent power')
            if source.phase_pu is not None or source.participation is not None:
                raise ValueError('balanced uncompensated source required')
            if any(any(v!=0 for v in row) for row in line.b_siemens) or line.temperature_c is not None or line.resistance_alpha_per_c is not None:
                raise ValueError('shunt capacitance and automatic cable temperature correction unsupported')
            payload=n.model_dump(mode='json')
            if baseline is not None and payload!=baseline:
                raise ValueError('mandatory cases must share the same owner network')
            baseline=payload
        # Reuse all existing event, motor, budget and engineering criteria checks.
        FeederDesignTask(id=self.id,line_id=self.line_id,mandatory_cases=self.mandatory_cases,
                         options=[dict(id='selected',resistance_ohm=.01,reactance_ohm=.01,provenance='validation placeholder only')])
        return self


def compile_equipment(task,selection):
    task=EquipmentDesignTask.model_validate(task)
    selection=Selection.model_validate(selection)
    static=calculate(task.sizing,selection)
    cable=next(c for c in task.sizing.cables if c.id==selection.cable_id)
    transformer=next(t for t in task.sizing.transformers if t.id==selection.transformer_id)
    zbase=task.sizing.voltage_v**2/(transformer.rating_kva*1000)
    rt=zbase*transformer.resistance_percent/100
    xt=zbase*math.sqrt(transformer.impedance_percent**2-transformer.resistance_percent**2)/100
    if xt<=0 or static['values']['cable_x_ohm']<=0:
        raise ValueError('dynamic profile requires positive source and cable reactance')
    cases=[]
    for original in task.mandatory_cases:
        payload=original.model_dump(mode='json');n=payload['study']['network']
        source=n['sources'][0]
        source.update(r_ohm=rt,x_ohm=xt,r0_ohm=None,x0_ohm=None)
        source['provenance'].update(source=transformer.source.reference,note=transformer.source.conditions)
        line=n['lines'][0]
        line.update(length_m=task.sizing.installation.length_m,rating_a=static['values']['derated_ampacity_a'],
                    emergency_rating_a=None,emergency_duration_s=None)
        line['provenance'].update(source=cable.source.reference,note=cable.source.conditions)
        payload['parameter_provenance']+='\nSelected cable and secondary-referred transformer: '+cable.source.reference+'; '+transformer.source.reference
        cases.append(payload)
    feeder=FeederDesignTask(id=task.id,line_id=task.line_id,mandatory_cases=cases,
        options=[dict(id='selected',resistance_ohm=static['values']['cable_r_ohm'],
                      reactance_ohm=static['values']['cable_x_ohm'],provenance=cable.source.reference)])
    # Preflight full three-grid compilation before any output is written.
    compile_design(feeder,dict(option_id='selected'))
    return task,selection,static,feeder


def combined_verdict(task,selection,static,dynamic):
    complete=dynamic['completed'];qualified=dynamic['numerically_qualified']
    accepted=qualified and static['passes_declared_checks'] and dynamic['meets_declared_dynamic_requirements']
    return dict(schema='power-grid-env.equipment-design-result/v1',
        task_sha256=digest(encoded(task.model_dump(mode='json'))),
        selection=selection.model_dump(mode='json'),
        status='incomplete' if not complete else 'numerically_unqualified' if not qualified else 'accepted' if accepted else 'rejected',
        completed=complete,numerically_qualified=qualified,
        static_checks_passed=static['passes_declared_checks'],dynamic_checks_passed=dynamic['meets_declared_dynamic_requirements'],
        meets_declared_requirements=bool(accepted),reward=1. if accepted else 0. if qualified else None,
        static_checks=static['checks'],dynamic_cases=dynamic['cases'],code_sha256=dynamic['code_sha256'],
        normative_approval=False,independent_physics_oracle=False,
        scope='balanced radial LV catalog selection; fixed supplied clearing time and derating; transformer secondary Thevenin equivalent; all mandatory three-grid motor studies; no transformer inrush, protection coordination, motor fault contribution or normative approval')


def run_equipment(task,selection,out,*,cancelled=None):
    task,selection,static,feeder=compile_equipment(task,selection)
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    (root/'task.json').write_bytes(encoded(task.model_dump(mode='json')))
    (root/'selection.json').write_bytes(encoded(selection.model_dump(mode='json')))
    run_sizing(task.sizing,selection,root/'sizing')
    # A static rejection must not suppress mandatory dynamic cases or label a
    # failed/incomplete solver as a physically qualified rejection.
    dynamic=run_design(feeder,dict(option_id='selected'),root/'dynamic',cancelled=cancelled)
    result=combined_verdict(task,selection,static,dynamic)
    (root/'result.json').write_bytes(encoded(result));seal(root)
    return result


def verify_equipment(root,*,expected_task=None,expected_selection=None):
    root=Path(root);before=verify(root)
    task,selection,static,feeder=compile_equipment(read_json(root/'task.json'),read_json(root/'selection.json'))
    if expected_task is not None and EquipmentDesignTask.model_validate(expected_task)!=task:
        raise ValueError('owner task mismatch')
    if expected_selection is not None and Selection.model_validate(expected_selection)!=selection:
        raise ValueError('owner selection mismatch')
    verify_sizing(root/'sizing',expected_task=task.sizing,expected_selection=selection)
    dynamic=verify_design(root/'dynamic',expected_task=feeder,expected_proposal=dict(option_id='selected'))
    result=combined_verdict(task,selection,static,dynamic)
    if read_json(root/'result.json')!=result:raise ValueError('combined verdict differs from evidence')
    if verify(root)!=before:raise ValueError('equipment evidence changed during verification')
    return dict(verified=True,owner_task_anchored=expected_task is not None,
                owner_selection_anchored=expected_selection is not None,manifest_sha256=before,**result)
