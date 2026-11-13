"""Conservative sampled no-pickup and discrete radial fault coordination screen.

This postprocessor does not insert relays into the motor simulation and never
exports a training reward or claims continuous-current/selectivity coverage.
"""
import cmath
import math
import shutil
from pathlib import Path
from typing import Literal
from pydantic import Field,model_validator
from power_grid_env.electrical_sizing import Contract,Source
from power_grid_env.equipment_catalog import verify_catalog,compile_catalog
from power_grid_env.design_study import compile_design
from power_grid_env.protection import iec_operate_time
from power_grid_env.topology import compile_topology
from power_grid_env.study_artifacts import encoded,read_json,seal,verify
from power_grid_env.evidence import package_fingerprint


class RelaySetting(Contract):
    source: Source
    pickup_a: float=Field(gt=0)
    curve: Literal['definite','standard_inverse','very_inverse','extremely_inverse']
    time_multiplier: float=Field(gt=0,le=10)
    pickup_tolerance: float=Field(ge=0,lt=.5)
    time_tolerance: float=Field(ge=0,lt=.5)
    breaker_opening_s: float=Field(ge=0,le=1)
    interrupting_a: float=Field(gt=0)


class CoordinationPlan(Contract):
    schema_version: Literal['power-grid-env.protection-screen/v1']='power-grid-env.protection-screen/v1'
    id: str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    catalog_manifest_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    primary: RelaySetting
    backup: RelaySetting
    grading_margin_s: float=Field(gt=0,le=2)
    sampled_start_margin: float=Field(ge=1,le=2)
    maximum_peak_relative_change: float=Field(default=.005,gt=0,le=.01)
    fault_positions: list[float]=Field(min_length=2,max_length=33)
    source_voltage_factors: list[float]=Field(min_length=1,max_length=9)

    @model_validator(mode='after')
    def points(self):
        if self.fault_positions!=sorted(set(self.fault_positions)) or self.fault_positions[0]!=0 or self.fault_positions[-1]!=1:
            raise ValueError('unique sorted fault positions must include 0 and 1')
        if self.source_voltage_factors!=sorted(set(self.source_voltage_factors)) or any(not .5<=v<=1.5 for v in self.source_voltage_factors):
            raise ValueError('unique sorted voltage factors between .5 and 1.5 required')
        return self


def timing(current,setting):
    """Worst pickup/time tolerance corners; no undocumented relay time floor."""
    s=RelaySetting.model_validate(setting)
    if not math.isfinite(current) or current<0:raise ValueError('finite nonnegative relay current required')
    earliest=iec_operate_time(current,s.pickup_a*(1-s.pickup_tolerance),s.curve,s.time_multiplier)*(1-s.time_tolerance)
    latest=iec_operate_time(current,s.pickup_a*(1+s.pickup_tolerance),s.curve,s.time_multiplier)*(1+s.time_tolerance)+s.breaker_opening_s
    return dict(earliest_trip_s=earliest if math.isfinite(earliest) else None,
                latest_clear_s=latest if math.isfinite(latest) else None)


def feeder_current(case,report):
    """Physical line current from two node phasors; includes auxiliary loads."""
    n=case.study.network;top=compile_topology(n);line=n.lines[0]
    left=top.terminal_bus[line.terminal_from];right=top.terminal_bus[line.terminal_to]
    z=complex(line.r_ohm[0][0],line.x_ohm[0][0]);scale=n.voltage_levels[0].nominal_ll_v/math.sqrt(3)
    channels=report['channels'];count=len(report['time_s'])
    arrays=[channels[left+'.v'],channels[left+'.a'],channels[right+'.v'],channels[right+'.a']]
    if any(len(a)!=count for a in arrays) or count<2:raise ValueError('incomplete voltage phasor trace')
    if any(not math.isfinite(v) for a in arrays for v in a):raise ValueError('nonfinite phasor trace')
    currents=[abs(scale*(cmath.rect(vl,al)-cmath.rect(vr,ar))/z) for vl,al,vr,ar in zip(*arrays)]
    if any(not math.isfinite(i) for i in currents):raise ValueError('nonfinite feeder current')
    return currents


def screen(plan,catalog_root):
    plan=CoordinationPlan.model_validate(plan);root=Path(catalog_root)
    before=verify(root)
    if before!=plan.catalog_manifest_sha256:raise ValueError('owner catalog manifest mismatch')
    verified=verify_catalog(root)
    _,_,selection,task,_=compile_catalog(read_json(root/'catalog.json'),read_json(root/'brief.json'),read_json(root/'selection.json'))
    # Use the exact emitted task, including catalog line and source parameters.
    from power_grid_env.equipment_design import compile_equipment
    feeder=compile_equipment(task,selection)[3]
    _,_,pack=compile_design(feeder,dict(option_id='selected'))
    starts=[]
    for case in pack.cases:
        peaks=[]
        for level in range(3):
            path=root/'equipment'/'dynamic'/'studies'/case.id/f'grid-{level}'
            # Impedances do not depend on refinement; parent verifier checked
            # each refined study and its full physical evidence already.
            peaks.append(max(feeder_current(case,read_json(path/'result.json'))))
        change=abs(peaks[2]-peaks[1])/max(peaks[2],1e-12)
        starts.append(dict(id=case.id,grid_peak_a=peaks,fine_peak_relative_change=change,
            numerically_stable=change<=plan.maximum_peak_relative_change,
            primary_no_pickup=plan.sampled_start_margin*max(peaks)<plan.primary.pickup_a*(1-plan.primary.pickup_tolerance),
            backup_no_pickup=plan.sampled_start_margin*max(peaks)<plan.backup.pickup_a*(1-plan.backup.pickup_tolerance)))
    sizing=task.sizing
    cable=next(c for c in sizing.cables if c.id==selection.cable_id)
    transformer=next(t for t in sizing.transformers if t.id==selection.transformer_id)
    network=pack.cases[0].study.network
    zsource=complex(network.sources[0].r_ohm,network.sources[0].x_ohm)
    line=network.lines[0];zline=complex(line.r_ohm[0][0],line.x_ohm[0][0])
    thermal_limit=(cable.k_a_sqrt_s_mm2*cable.area_mm2)**2
    faults=[]
    for position in plan.fault_positions:
        for voltage_factor in plan.source_voltage_factors:
            current=voltage_factor*sizing.voltage_v/(math.sqrt(3)*abs(zsource+position*zline))
            primary=timing(current,plan.primary);backup=timing(current,plan.backup)
            pt=primary['latest_clear_s'];bt=backup['latest_clear_s'];be=backup['earliest_trip_s']
            grading=be-pt if be is not None and pt is not None else None
            checks=dict(primary_pickup=pt is not None,backup_pickup=bt is not None,
                grading=grading is not None and grading>=plan.grading_margin_s,
                owner_clearing_budget=pt is not None and pt<=sizing.installation.clearing_s,
                primary_interrupting=current<=min(plan.primary.interrupting_a,sizing.installation.breaker_interrupting_a),
                backup_interrupting=current<=plan.backup.interrupting_a,
                primary_cable_i2t=pt is not None and pt<=5 and current**2*pt<=thermal_limit,
                backup_cable_i2t=bt is not None and bt<=5 and current**2*bt<=thermal_limit,
                primary_transformer_withstand=pt is not None and pt<=transformer.short_time_s and current<=transformer.short_time_a,
                backup_transformer_withstand=bt is not None and bt<=transformer.short_time_s and current<=transformer.short_time_a)
            faults.append(dict(position_pu=position,source_voltage_factor=voltage_factor,current_a=current,
                primary=primary,backup=backup,grading_s=grading,checks=checks,passed=all(checks.values())))
    qualified=verified['equipment']['numerically_qualified'] and all(r['numerically_stable'] for r in starts)
    checks=dict(catalog_requirements=verified['equipment']['meets_declared_requirements'],
        sampled_start_no_pickup=all(r['primary_no_pickup'] and r['backup_no_pickup'] for r in starts),
        sampled_fault_coordination=all(r['passed'] for r in faults))
    if verify(root)!=before:raise ValueError('catalog evidence changed during screening')
    result=dict(schema='power-grid-env.protection-screen-result/v1',catalog_manifest_sha256=before,
        code_sha256=package_fingerprint(),numerically_qualified=bool(qualified),checks=checks,
        passes_declared_screen=bool(qualified and all(checks.values())),starts=starts,faults=faults,
        reward=None,closed_loop_protection_qualified=False,normative_approval=False,
        scope='conservative sampled no-pickup; discrete constant symmetrical RMS faults on selected hot-impedance radial feeder; pickup/time tolerance corners and breaker opening; backup thermal duty on primary failure; no cold/hot envelope, motor fault contribution, continuous extrema or closed-loop relay action')
    encoded(result);return result


def run_screen(plan,catalog_root,out):
    plan=CoordinationPlan.model_validate(plan);source=Path(catalog_root);root=Path(out)
    if root.resolve().is_relative_to(source.resolve()):raise ValueError('screen output must be outside source artifact')
    result=screen(plan,source)
    root.mkdir(parents=True,exist_ok=False)
    shutil.copytree(source,root/'catalog-study')
    if verify(root/'catalog-study')!=plan.catalog_manifest_sha256:raise ValueError('copied catalog changed')
    (root/'plan.json').write_bytes(encoded(plan.model_dump(mode='json')))
    (root/'result.json').write_bytes(encoded(result));seal(root);return result


def verify_screen(root,*,expected_plan=None):
    root=Path(root);before=verify(root);plan=CoordinationPlan.model_validate(read_json(root/'plan.json'))
    if expected_plan is not None and CoordinationPlan.model_validate(expected_plan)!=plan:raise ValueError('owner protection plan mismatch')
    result=screen(plan,root/'catalog-study')
    if read_json(root/'result.json')!=result:raise ValueError('protection screen evidence differs')
    if verify(root)!=before:raise ValueError('protection evidence changed during verification')
    return dict(verified=True,owner_plan_anchored=expected_plan is not None,manifest_sha256=before,**result)
