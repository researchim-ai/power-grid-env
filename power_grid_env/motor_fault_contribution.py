"""Frozen-EMF three-phase RMS fault attribution on a radial feeder.

Complex superposition preserves passive motor impedances when suppressing an
EMF. This is an instantaneous algebraic snapshot, not a sustained fault rating.
"""
import cmath
import math
from pathlib import Path

import numpy as np
from pydantic import Field, model_validator
from power_grid_env.electrical_sizing import Contract, Source
from power_grid_env.study_artifacts import encoded, digest, read_json, seal, verify
from power_grid_env.evidence import package_fingerprint


class Phasor(Contract):
    real: float
    imag: float

    def value(self):
        return complex(self.real,self.imag)


def phasor(z):
    return dict(real=float(z.real),imag=float(z.imag),magnitude=float(abs(z)))


class MotorSnapshot(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    source: Source
    rated_va: float = Field(gt=0)
    rated_ll_v: float = Field(gt=0)
    stator_r_pu: float = Field(gt=0)
    stator_x_pu: float = Field(gt=0)
    rotor_x_pu: float = Field(gt=0)
    magnetizing_x_pu: float = Field(gt=0)
    emf_pu: Phasor
    connected: bool

    def impedance(self):
        xp=self.stator_x_pu+self.rotor_x_pu*self.magnetizing_x_pu/(self.rotor_x_pu+self.magnetizing_x_pu)
        return complex(self.stator_r_pu,xp)*self.rated_ll_v**2/self.rated_va


class FaultSnapshot(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    source: Source
    nominal_ll_v: float = Field(gt=0)
    source_emf_v: Phasor  # phase-to-neutral RMS, not line-to-line
    source_r_ohm: float = Field(ge=0)
    source_x_ohm: float = Field(gt=0)
    source_connected: bool = True
    line_r_ohm: float = Field(ge=0)
    line_x_ohm: float = Field(gt=0)
    fault_position_pu: float = Field(ge=0,le=1)
    fault_r_ohm: float = Field(ge=0)
    fault_x_ohm: float = Field(ge=0)
    downstream_load_p_w: float = Field(ge=0)
    downstream_load_q_var: float = Field(ge=0)
    motors: list[MotorSnapshot] = Field(max_length=128)

    @model_validator(mode='after')
    def motors_unique(self):
        if len({m.id for m in self.motors})!=len(self.motors):
            raise ValueError('duplicate motor id')
        if any(not math.isclose(m.rated_ll_v,self.nominal_ll_v,rel_tol=1e-10) for m in self.motors):
            raise ValueError('motor voltage base differs from feeder')
        return self


def calculate(snapshot):
    s=FaultSnapshot.model_validate(snapshot)
    # Two endpoint nodes, plus an interior fault node when necessary.
    p=s.fault_position_pu
    f=0 if p==0 else 1 if p==1 else 2
    size=2 if f!=2 else 3
    y=np.zeros((size,size),complex)
    zs=complex(s.source_r_ohm,s.source_x_ohm)
    zl=complex(s.line_r_ohm,s.line_x_ohm)
    branches=[(0,1,zl)] if f!=2 else [(0,f,p*zl),(f,1,(1-p)*zl)]
    for left,right,z in branches:
        g=1/z;y[left,left]+=g;y[right,right]+=g;y[left,right]-=g;y[right,left]-=g
    ys=1/zs if s.source_connected else 0j
    y[0,0]+=ys
    yload=complex(s.downstream_load_p_w,-s.downstream_load_q_var)/s.nominal_ll_v**2
    y[1,1]+=yload
    ym=[1/m.impedance() if m.connected else 0j for m in s.motors]
    y[1,1]+=sum(ym)
    zf=complex(s.fault_r_ohm,s.fault_x_ohm)
    if zf:y[f,f]+=1/zf
    # Bolted node is constrained to zero, without a fictitious tiny resistor.
    free=[i for i in range(size) if zf or i!=f]
    labels=['external_source']+['motor:'+m.id for m in s.motors]
    emfs=[s.source_emf_v.value()]+[m.emf_pu.value()*m.rated_ll_v/math.sqrt(3) for m in s.motors]
    admittances=[ys]+ym
    nodes=[0]+[1]*len(s.motors)
    components=[];solutions=[];injections=[]
    def currents(v,ems):
        source=ys*(ems[0]-v[0])
        motors=[g*(e-v[1]) for g,e in zip(ym,ems[1:])]
        left=source if f==0 else (v[0]-v[f])/(p*zl)
        right=-sum(motors)+yload*v[1] if f==1 else (v[f]-v[1])/((1-p)*zl)
        fault=source+sum(motors)-yload*v[1] if not zf else v[f]/zf
        # Segment orientations: source -> fault -> motor bus.
        return dict(source_a=source,upstream_segment_a=left,downstream_segment_a=right,
                    fault_a=fault,load_a=yload*v[1],
                    feeder_from_a=right if f==0 else left,
                    feeder_to_a=left if f==1 else right,
                    **{'motor:'+m.id+'_a':i for m,i in zip(s.motors,motors)})
    for index,label in enumerate(labels):
        rhs=np.zeros(size,complex);rhs[nodes[index]]=emfs[index]*admittances[index]
        v=np.zeros(size,complex)
        try:v[free]=np.linalg.solve(y[np.ix_(free,free)],rhs[free])
        except np.linalg.LinAlgError as exc:raise ValueError('unreferenced or singular fault snapshot') from exc
        active=[0j]*len(emfs);active[index]=emfs[index]
        values=currents(v,active)
        components.append(dict(excitation=label,currents={k:phasor(z) for k,z in values.items()}))
        solutions.append(v);injections.append(rhs)
    total_v=sum(solutions,np.zeros(size,complex))
    total=currents(total_v,emfs)
    residual=(y@total_v-sum(injections,np.zeros(size,complex)))[free]
    kcl=total['source_a']+sum(total['motor:'+m.id+'_a'] for m in s.motors)-total['load_a']-total['fault_a']
    superposition=max(abs(total[k]-sum(complex(c['currents'][k]['real'],c['currents'][k]['imag']) for c in components)) for k in total)
    scale=max(1.,*(abs(z) for z in total.values()))
    audits=dict(kcl_error_a=float(abs(kcl)),nodal_residual_a=float(max(abs(residual),default=0)),
                superposition_error_a=float(superposition),tolerance_a=1e-8*scale)
    result=dict(schema='power-grid-env.motor-fault-contribution/v1',
                input_sha256=digest(encoded(s.model_dump(mode='json'))),code_sha256=package_fingerprint(),
                voltages_v={('upstream' if i==0 else 'downstream' if i==1 else 'fault'):phasor(z) for i,z in enumerate(total_v)},
                currents={k:phasor(z) for k,z in total.items()},components=components,audits=audits,
                segment_lengths_pu=dict(upstream=p,downstream=1-p),
                passed_algebra_audits=all(audits[k]<=audits['tolerance_a'] for k in ['kcl_error_a','nodal_residual_a','superposition_error_a']),
                normative_approval=False,reward=None,
                scope='Frozen single-cage rotor EMFs, balanced algebraic stator, downstream constant-Z loads; separate physical branch currents and linear excitation contributions. No independent steady initialization, stator/DC transients, sustained RMS rating, decay envelope, relay qualification or thermal duty.')
    encoded(result)
    return result


def snapshot_from_report(study,report,index):
    """Extract one supported native trace point without inferring rotor EMFs.

    The full input report hash is captured as provenance. This function does
    not verify a study artifact or assert that the trace is physically valid.
    """
    from power_grid_env.study_spec import StudySpec
    from power_grid_env.topology import compile_topology
    from power_grid_env.rms_study import positive_sequence
    study=StudySpec.model_validate(study)
    n,c=study.network,study.coupled
    if c is None or c.machines or n.generators or len(n.sources)!=1 or len(n.lines)!=1:
        raise ValueError('one source/line and motor-only coupled study required')
    if n.transformers or n.shunts or n.inverters or n.storages or n.neutral_grounds or n.harmonics or n.study_faults:
        raise ValueError('unsupported additional equipment')
    top=compile_topology(n);line=n.lines[0];source=n.sources[0]
    upstream=top.terminal_bus[source.terminal_id]
    left=top.terminal_bus[line.terminal_from];right=top.terminal_bus[line.terminal_to]
    if len(top.buses)!=2 or left!=upstream or right==upstream:
        raise ValueError('oriented two-bus radial line required')
    if any(top.node_to_bus[m.node_id]!=right or m.model!='single_cage' for m in c.motors):
        raise ValueError('downstream single-cage motors required')
    if any(top.terminal_bus[l.terminal_id]!=right or l.model!='constant_z' for l in n.loads):
        raise ValueError('downstream constant-Z loads required')
    if np.any(line.b_siemens):raise ValueError('line charging unsupported')
    if source.phase_pu is not None:raise ValueError('unbalanced source unsupported')
    for load in n.loads:
        for powers in [load.p_w,load.q_var]:
            if set(powers)!=set('ABC') or not np.allclose(list(powers.values()),next(iter(powers.values())),rtol=1e-10,atol=1e-6):
                raise ValueError('balanced three-phase load required')
        if load.cold_load_multiplier is not None or load.cold_load_tau_s is not None or load.rated_kv is not None:
            raise ValueError('load modifiers unsupported')
    if not 0<=index<len(report['time_s']):raise ValueError('sample index out of range')
    channels=report['channels']
    def at(key):
        a=channels[key]
        if len(a)!=len(report['time_s']) or not math.isfinite(a[index]):
            raise ValueError('invalid trace channel '+key)
        return a[index]
    def closed(key):
        value=at(key)
        if value not in (0,1):raise ValueError('nonbinary trace state '+key)
        return bool(value)
    if not closed(line.id+'.closed'):raise ValueError('open feeder snapshot unsupported')
    active=[f for f in c.faults if closed(f.id+'.active')]
    if len(active)!=1:raise ValueError('exactly one active fault required')
    fault=active[0];bus=top.node_to_bus[fault.node_id]
    if bus not in (left,right):raise ValueError('unknown fault bus')
    scale=1.;t=report['time_s'][index];side=report['sample_side'][index]
    if side not in ('initial','regular','pre_event','post_event'):raise ValueError('unknown sample side')
    for e in sorted(c.events,key=lambda e:e.time_s):
        if e.kind=='source_voltage' and e.equipment_id==source.id and (t>e.time_s+1e-12 or abs(t-e.time_s)<=1e-12 and side=='post_event'):
            scale=e.value
    z=positive_sequence(np.array(line.r_ohm)+1j*np.array(line.x_ohm))
    evidence=dict(reference='trace-sha256:'+digest(encoded(report)),
                  conditions=f'Frozen native EMFs at sample {index}, t={t}, side={side}; algebraic audit only')
    emf=cmath.rect(source.ll_v*scale/math.sqrt(3),source.angle_a_rad)
    return FaultSnapshot.model_validate(dict(id='trace-snapshot',source=evidence,
        nominal_ll_v=top.buses[left].nominal_ll_v,source_emf_v=dict(real=emf.real,imag=emf.imag),
        source_r_ohm=source.r_ohm,source_x_ohm=source.x_ohm,
        source_connected=closed(source.id+'.connected'),line_r_ohm=z.real,line_x_ohm=z.imag,
        fault_position_pu=0 if bus==left else 1,fault_r_ohm=fault.r_ohm,fault_x_ohm=fault.x_ohm,
        downstream_load_p_w=sum(sum(l.p_w.values()) for l in n.loads),
        downstream_load_q_var=sum(sum(l.q_var.values()) for l in n.loads),
        motors=[dict(id=m.id,source=evidence,rated_va=m.rated_va,rated_ll_v=m.rated_ll_v,
            stator_r_pu=m.stator_r_pu,stator_x_pu=m.stator_x_pu,rotor_x_pu=m.rotor_x_pu,
            magnetizing_x_pu=m.magnetizing_x_pu,
            emf_pu=dict(real=at(m.id+'.e_real_pu'),imag=at(m.id+'.e_imag_pu')),
            connected=closed(m.id+'.connected')) for m in c.motors])).model_dump(mode='json')


def run(snapshot,out):
    s=FaultSnapshot.model_validate(snapshot).model_dump(mode='json');result=calculate(s)
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for name,value in [('snapshot',s),('result',result)]:
        (root/(name+'.json')).write_bytes(encoded(value))
    seal(root);return result


def verify_contribution(root,*,expected_snapshot):
    root=Path(root);before=verify(root)
    owner=FaultSnapshot.model_validate(expected_snapshot).model_dump(mode='json')
    if read_json(root/'snapshot.json')!=owner:raise ValueError('owner snapshot mismatch')
    result=calculate(owner)
    if read_json(root/'result.json')!=result:raise ValueError('contribution result mismatch')
    if before!=verify(root):raise ValueError('artifact changed during verification')
    return dict(verified=True,manifest_sha256=before,**result)


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True);p.add_argument('--out',required=True)
    args=p.parse_args();r=run(read_json(args.input),args.out)
    print(encoded(r).decode())
    if not r['passed_algebra_audits']:raise SystemExit(2)

if __name__=='__main__':main()
