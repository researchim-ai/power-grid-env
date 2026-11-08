"""Procedural, topology-grouped tasks for physically verified LLM plans.

Private tasks are verifier inputs. Only compact_public_network and observations
are allowed into prompts. Family signatures ignore identifiers/electrical values.
"""
import random
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field,model_validator

from power_grid_env.build import NetBuilder
from power_grid_env.model import Network
from power_grid_env.scenarios import Scenario
from power_grid_env.study_artifacts import digest,encoded


class TopologyFamily(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id:str=Field(pattern=r'^[a-z][a-z0-9_-]{0,31}$')
    split:Literal['train','dev','test']
    parents:list[int]=Field(min_length=2,max_length=24)

    @model_validator(mode='after')
    def rooted(self):
        if self.parents[0]!=-1 or any(type(p) is not int or not 0<=p<i for i,p in enumerate(self.parents[1:],1)):
            raise ValueError('one rooted ordered tree required: first -1, then parent index smaller than child')
        return self

    def topology_group(self):
        def subtree(i):
            children=sorted(subtree(j) for j,p in enumerate(self.parents) if p==i)
            return ('T' if i==len(self.parents)-1 else 'N')+'('+''.join(children)+')'
        # T marks the endpoint of the normally open backup connection.
        return digest(subtree(0).encode())


def default_families():
    return [TopologyFamily(id='chain',split='train',parents=[-1,0,1]),
            TopologyFamily(id='fork',split='train',parents=[-1,0,0]),
            TopologyFamily(id='branched',split='dev',parents=[-1,0,0,1,1]),
            TopologyFamily(id='deep',split='test',parents=[-1,0,0,1,2,3,4])]


class LLMGenerationPlan(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    schema_version:Literal['power-grid-env.llm-generation/v1']='power-grid-env.llm-generation/v1'
    seed:int=Field(default=31,ge=0,le=2**32-1)
    variants_per_family:int=Field(default=3,ge=1,le=64)
    families:list[TopologyFamily]=Field(default_factory=default_families,min_length=3,max_length=64)

    @model_validator(mode='after')
    def split_contract(self):
        if len(self.families)*self.variants_per_family>256:raise ValueError('at most 256 generated tasks per corpus')
        if sum(len(f.parents) for f in self.families)*self.variants_per_family>1536:raise ValueError('corpus topology-size budget exceeded; generate separate bounded shards')
        if {f.split for f in self.families}!={'train','dev','test'}:raise ValueError('train/dev/test topology families required')
        if len({f.id for f in self.families})!=len(self.families):raise ValueError('unique family IDs required')
        if len({f.topology_group() for f in self.families})!=len(self.families):
            raise ValueError('duplicate or isomorphic topology families; parameter/ID changes do not create an independent split')
        return self


class LLMTask(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    schema_version:Literal['power-grid-env.llm-task/v1']='power-grid-env.llm-task/v1'
    id:str=Field(pattern=r'^[a-f0-9]{24}$')
    split:Literal['train','dev','test']
    topology_group:str=Field(pattern=r'^[a-f0-9]{64}$')
    network:Network
    scenario:Scenario

    @model_validator(mode='after')
    def bounded_profile(self):
        s=self.scenario
        if s.horizon_s!=80 or s.control_interval_s!=10 or s.settle_s!=10:
            raise ValueError('LLM v1 profile uses a fixed 80 s horizon and 10 s control/settling interval')
        if s.load_profile or s.protection or s.capacitor_controls or s.maintenance_switch_ids or s.model_mismatch_scale or s.unrestorable_load_ids or s.trace_limit is not None:
            raise ValueError('LLM v1 task excludes hidden future schedules, automatic devices, mismatch and trace truncation')
        if s.what_if_quota!=2 or s.drop_probability or s.meter_delay_s or s.measurement_period_s is not None:
            raise ValueError('LLM v1 uses fresh complete telemetry and two public-model what-if calls')
        if not s.fault_disclosed or s.fault_line_id is None or not s.radiality:
            raise ValueError('one disclosed fault section and radial operation required')
        if len(self.network.switches)>16 or len(self.network.loads)>32 or any(sw.operate_time_s!=0 for sw in self.network.switches):
            raise ValueError('bounded small feeder with explicitly instantaneous switching required')
        if len(self.network.connectivity_nodes)>64 or len(self.network.lines)>64 or self.network.study_faults:
            raise ValueError('bounded base network without additional study faults required')
        if self.network.inverters or self.network.storages or self.network.generators or self.network.transformers or self.network.shunts:
            raise ValueError('LLM v1 profile contains passive lines/loads, switches and one source')
        if len(self.network.sources)!=1 or any(not meter.available or meter.delay_s for meter in self.network.meters):
            raise ValueError('one source and available zero-delay meters required')
        if s.fault_kind!='3ph' or s.fault_phases!=['A','B','C'] or not 1<=s.fault_r_ohm<=5:
            raise ValueError('LLM v1 fault profile is disclosed three-phase, 1 to 5 ohm')
        terminals={t.id:t.connectivity_node_id for t in self.network.terminals}
        measured={m.connectivity_node_id for m in self.network.meters if m.measures_voltage}
        if any(terminals[l.terminal_id] not in measured or l.cold_load_multiplier is not None or l.model!='constant_pq' for l in self.network.loads):
            raise ValueError('constant-PQ loads with complete voltage telemetry and no cold-load schedules required')
        if sum(sum(load.p_w.values()) for load in self.network.loads)<=0:raise ValueError('positive constant demand required')
        return self


def generate_task(plan,family,index):
    entropy=digest(encoded(dict(seed=plan.seed,family=family.id,index=index)))
    rng=random.Random(int(entropy[:16],16));identifier=entropy[:24]
    def name(role):return role[0]+'_'+digest((identifier+role).encode())[:12]
    b=NetBuilder(identifier,'Authored synthetic LLM feeder',12.47)
    nodes={role:name(role) for role in ['grid','fault_up','fault_down','backup']+[f'node{i}' for i in range(len(family.parents))]}
    for node in nodes.values():b.node(node)
    b.source(nodes['grid'],.05,.1,source_id=name('source'))
    b.switch(nodes['grid'],nodes['fault_up'],switch_id=name('breaker_up'))
    fault=b.line_z1(nodes['fault_up'],nodes['fault_down'],complex(rng.uniform(.08,.16),rng.uniform(.06,.12)),line_id=name('damaged_line'))
    b.switch(nodes['fault_down'],nodes['node0'],switch_id=name('breaker_down'))
    for i,parent in enumerate(family.parents[1:],1):
        b.line_z1(nodes[f'node{parent}'],nodes[f'node{i}'],complex(rng.uniform(.06,.2),rng.uniform(.04,.12)),line_id=name('feeder'+str(i)))
    b.line_z1(nodes['grid'],nodes['backup'],.12+.08j,line_id=name('backup_line'),rating_a=.5 if index%3==1 else 400.)
    b.switch(nodes['backup'],nodes['node'+str(len(family.parents)-1)],switch_id=name('tie'),closed=False,locked=index%3==2)
    for i,role in enumerate(['grid']+[f'node{j}' for j in range(len(family.parents))]):
        p=rng.uniform(20000.,40000.)
        factors=[rng.uniform(.8,1.2) for _ in range(3)];total=sum(factors)
        b.load(nodes[role],{ph:p*f/total for ph,f in zip('ABC',factors)},.25*p,load_id=name('load'+str(i)),critical=i==0)
        b.meter(nodes[role],meter_id=name('meter'+str(i)))
    network=b.build()
    scenario=Scenario(id=identifier,seed=int(entropy[16:24],16),horizon_s=80,control_interval_s=10,settle_s=10,
        fault_line_id=fault,fault_r_ohm=rng.uniform(1.,5.),what_if_quota=2,
        hidden_canary='PRIVATE_ONLY_'+digest((identifier+'verifier').encode()),description='')
    return LLMTask(id=identifier,split=family.split,topology_group=family.topology_group(),network=network,scenario=scenario)


def compact_public_network(network):
    terminals={t.id:t.connectivity_node_id for t in network.terminals}
    levels={v.id:v.nominal_ll_v for v in network.voltage_levels}
    return dict(frequency_hz=network.frequency_hz,
        nodes=[dict(id=n.id,phases=n.phases,nominal_ll_v=levels[n.voltage_level_id]) for n in network.connectivity_nodes],
        sources=[dict(id=s.id,node=terminals[s.terminal_id],ll_v=s.ll_v,r_ohm=s.r_ohm,x_ohm=s.x_ohm) for s in network.sources],
        lines=[dict(id=l.id,from_node=terminals[l.terminal_from],to_node=terminals[l.terminal_to],r_ohm=l.r_ohm,x_ohm=l.x_ohm,rating_a=l.rating_a) for l in network.lines],
        switches=[dict(id=s.id,from_node=terminals[s.terminal_from],to_node=terminals[s.terminal_to],kind=s.kind,closed=s.closed,operable=s.operable,locked=s.locked,rated_current_a=s.rated_current_a,interrupt_current_a=s.interrupt_current_a) for s in network.switches],
        loads=[dict(id=l.id,node=terminals[l.terminal_id],p_w=l.p_w,q_var=l.q_var,critical=l.critical) for l in network.loads],
        meters=[dict(id=m.id,node=m.connectivity_node_id) for m in network.meters])
