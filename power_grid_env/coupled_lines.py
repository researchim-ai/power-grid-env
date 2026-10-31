"""Line-end switching on a fixed bus map, with source-reachability qualification."""
from collections import defaultdict
import math
import numpy as np
from power_grid_env.rms_study import positive_sequence

LINE_EVENTS={'line_open','line_close'}
CHANNELS=['closed','from_current_a','to_current_a','from_p_w','from_q_var','to_p_w','to_q_var','loss_p_w','net_q_var']


def line_graph(network,topology,closed):
    adjacency={bus:set() for bus in topology.buses}
    for line in network.lines:
        if closed[line.id]:
            a,b=topology.terminal_bus[line.terminal_from],topology.terminal_bus[line.terminal_to]
            adjacency[a].add(b);adjacency[b].add(a)
    for tr in network.transformers:
        a,b=[topology.terminal_bus[w.terminal_id] for w in tr.windings]
        adjacency[a].add(b);adjacency[b].add(a)
    return adjacency


def disconnected_buses(network,topology,closed):
    graph=line_graph(network,topology,closed)
    start=topology.terminal_bus[network.sources[0].terminal_id]
    seen={start};pending=[start]
    while pending:
        for bus in graph[pending.pop()]-seen:seen.add(bus);pending.append(bus)
    return sorted(set(graph)-seen)


def validate_line_events(network,topology,events):
    errors=[];closed={line.id:True for line in network.lines};groups=defaultdict(list)
    for event in events:
        if event.kind in LINE_EVENTS:groups[event.time_s].append(event)
    for time,group in sorted(groups.items()):
        targets=set()
        for event in group:
            if event.equipment_id not in closed:errors.append('unknown switched line');continue
            if event.equipment_id in targets:errors.append('conflicting simultaneous line operations');continue
            targets.add(event.equipment_id)
            requested=event.kind=='line_close'
            if closed[event.equipment_id]==requested:errors.append('redundant line operation')
            if event.value is not None:errors.append('line operation takes no value')
            closed[event.equipment_id]=requested
        try:
            if disconnected_buses(network,topology,closed):errors.append(f'line switching at {time} would create an unsupported island')
        except (KeyError,IndexError,ValueError):errors.append('invalid line switching connectivity model')
    return errors


class CoupledLines:
    def __init__(self,network,topology,base_y,bus_names):
        self.network=network;self.topology=topology;self.original=base_y.copy();self.names=bus_names
        self.closed={line.id:True for line in network.lines};self.parameters={}
        lookup={bus:i for i,bus in enumerate(bus_names)}
        for line in network.lines:
            left,right=topology.terminal_bus[line.terminal_from],topology.terminal_bus[line.terminal_to]
            a,b=lookup[left],lookup[right];ll=topology.buses[left].nominal_ll_v;zb=ll**2/network.s_base_va
            z=positive_sequence(np.asarray(line.r_ohm)+1j*np.asarray(line.x_ohm))/zb
            shunt=1j*positive_sequence(line.b_siemens).real*zb
            self.parameters[line.id]=(a,b,1/z,shunt,ll)

    def passive_matrix(self):
        if disconnected_buses(self.network,self.topology,self.closed):raise ValueError('runtime line switching would create an unsupported island')
        matrix=self.original.tolil(copy=True)
        for id_,(a,b,y,shunt,_) in self.parameters.items():
            if not self.closed[id_]:
                matrix[a,a]-=y+shunt/2;matrix[b,b]-=y+shunt/2
                matrix[a,b]+=y;matrix[b,a]+=y
        return matrix.tocsc()

    def apply(self,event):
        if event.kind not in LINE_EVENTS:return None
        self.closed[event.equipment_id]=event.kind=='line_close'
        return dict(outcome='applied',closed=self.closed[event.equipment_id],switching='both line ends; series and charging removed/restored')

    def channels(self):return [id_+'.'+key for id_ in self.parameters for key in CHANNELS]

    def signals(self,voltage):
        result={}
        for id_,(a,b,y,shunt,ll) in self.parameters.items():
            ia=((voltage[a]-voltage[b])*y+voltage[a]*shunt/2) if self.closed[id_] else 0j
            ib=((voltage[b]-voltage[a])*y+voltage[b]*shunt/2) if self.closed[id_] else 0j
            sa=voltage[a]*np.conj(ia)*self.network.s_base_va;sb=voltage[b]*np.conj(ib)*self.network.s_base_va
            ibase=self.network.s_base_va/(math.sqrt(3)*ll)
            values=[self.closed[id_],abs(ia)*ibase,abs(ib)*ibase,sa.real,sa.imag,sb.real,sb.imag,(sa+sb).real,(sa+sb).imag]
            result.update({id_+'.'+key:float(value) for key,value in zip(CHANNELS,values)})
        return result
