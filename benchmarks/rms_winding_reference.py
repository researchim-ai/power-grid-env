"""Independent SI winding electrothermal RMS reference for radial motor protection.

No native currents, states, event times, relay helpers or event engine are used
in integration. Owner passports and schedules are the only inputs. This is a
bounded balanced single-cage profile, not an OEM or normative certificate.
"""
import copy
import heapq
import math

import numpy as np
from scipy.integrate import solve_ivp
from scipy.linalg import lu_factor, lu_solve

from benchmarks.motor_only_loaded_reference import WindingReference
from power_grid_env.study_spec import StudySpec


class ElectrothermalReference:
    def __init__(self, study):
        self.study = StudySpec.model_validate(study)
        self.h=self.study.coupled.motors[0].winding_thermal
        if self.h is None:raise ValueError('winding thermal passport required')
        self.initial_temperatures=self.h.initial_temperature_c or (self.h.ambient_c,self.h.ambient_c)
        s = self.study.coupled
        if s is None or s.network_automation is None:
            raise ValueError('loaded radial automation required')
        if len(self.study.network.lines) != 1 or len(self.study.network.sources) != 1:
            raise ValueError('one source and one line required')
        self.auto = s.network_automation
        self.relays = sorted(self.auto.relays, key=lambda r: r.id)
        self.breakers = {b.id: b for b in self.auto.breakers}
        if self.auto.transfers or any(b.maximum_reclose_shots or b.initial_open or b.target_kind not in ('source', 'line') for b in self.breakers.values()):
            raise ValueError('only initially closed source/line breakers without ATS/reclose supported')
        line = self.study.network.lines[0].id
        source = self.study.network.sources[0].id
        for b in self.breakers.values():
            if b.target_id != (line if b.target_kind == 'line' else source):
                raise ValueError('unknown breaker target')
            if any(k not in self.breakers for k in b.backup_breaker_ids):
                raise ValueError('unknown backup breaker')
        curves = {'standard_inverse', 'very_inverse', 'extremely_inverse', 'definite'}
        for r in self.relays:
            if r.kind != 'overcurrent' or r.directional or r.curve not in curves or r.measurement_kind not in ('line', 'source'):
                raise ValueError('non-directional line/source OC required')
            if r.measurement_id != (line if r.measurement_kind == 'line' else source) or r.breaker_id not in self.breakers:
                raise ValueError('unknown relay target')
        if any(e.kind not in ('relay_measurement_lost', 'relay_measurement_restored') or e.equipment_id not in {r.id for r in self.relays} for e in s.events):
            raise ValueError('only scheduled CT availability commands supported')
        physical = self.study.model_dump(mode='json')
        physical['coupled']['events'] = []
        m=physical['coupled']['motors'][0];m.pop('winding_thermal',None)
        self.rs0=m['stator_r_pu']*m['rated_ll_v']**2/m['rated_va']
        self.rr0=m['rotor_r_pu']*m['rated_ll_v']**2/m['rated_va']
        m['stator_r_pu']*=1+self.h.alpha_per_c*(self.initial_temperatures[0]-self.h.reference_c)
        m['rotor_r_pu']*=1+self.h.alpha_per_c*(self.initial_temperatures[1]-self.h.reference_c)
        cold=physical['coupled']['operating_point'] is None
        if cold:physical['coupled']['operating_point']={'method':'high_speed_equilibrium','minimum_speed_pu':.8,'thermal_state':'passport'}
        for r in physical['coupled']['network_automation']['relays']:
            r['measurement_available'] = True
        self.w = WindingReference(physical)
        if cold:
            self.w.initialize=lambda: (np.array([0.,0.,0.,self.w.m.thermal.initial_level if self.w.m.thermal else 0.]),dict(mode='cold_zero_flux'))
        if self.w.yl.real <= 0:
            raise ValueError('positive downstream passive load required for island reference')
        n, m = self.study.network, self.w.m
        if len({(b.target_kind, b.target_id) for b in self.breakers.values()}) != len(self.breakers):
            raise ValueError('one actuator per physical branch required')
        for matrix in (n.lines[0].r_ohm, n.lines[0].x_ohm):
            a = np.asarray(matrix)
            if a.shape != (3, 3) or not np.allclose(a, np.eye(3)*(a[0, 0]-a[0, 1])+np.full((3, 3), a[0, 1]), rtol=0., atol=1e-14):
                raise ValueError('balanced symmetric phase impedance required')
        node = next(v for v in n.connectivity_nodes if v.id == m.node_id)
        level = next(v for v in n.voltage_levels if v.id == node.voltage_level_id)
        if level.nominal_ll_v != m.rated_ll_v or any(v.rated_kv is not None and v.rated_kv*1000 != m.rated_ll_v for v in n.loads):
            raise ValueError('motor/bus/passive load voltage bases must coincide')
        def visit(id, stack):
            if id in stack:
                raise ValueError('cyclic breaker backup graph unsupported')
            for backup in self.breakers[id].backup_breaker_ids:
                visit(backup, stack | {id})
        for id in self.breakers:
            visit(id, set())
        self.factors = {}

    def circuit(self, x, c):
        w = self.w
        key = (c['on'], c['line'], c['source'], tuple(sorted(c['faults'])),float(x[-2]),float(x[-1]))
        if key not in self.factors:
            up = down = 0j
            for key_fault in c['faults']:
                f = w.faults[key_fault]
                y = 1 / complex(f.r_ohm, f.x_ohm)
                if f.node_id == w.up:
                    up += y
                else:
                    down += y
            yl = 1 / w.zl if c['line'] else 0j
            ys = 1 / w.zs if c['source'] else 0j
            self.factors.clear()
            rs=self.rs0*(1+self.h.alpha_per_c*(x[-2]-self.h.reference_c))
            a = np.array([[rs + 1j*w.wb*w.ls, 1j*w.wb*w.lm, -1, 0],
                          [w.lm, w.lr, 0, 0], [1, 0, w.yl+down+yl, -yl],
                          [0, 0, -yl, yl+ys+up]], complex)
            if not c['on']:
                a[0] = [1, 0, 0, 0]
            # An empty de-energized node has no physical voltage coordinate.
            # Restrict that coordinate to zero; do not add a grounding shunt.
            if not np.any(a[3]):
                a[3, 3] = 1
            self.factors[key] = lu_factor(a)
        im, ir, vd, vu = lu_solve(self.factors[key], [0, complex(x[0], x[1]), 0, w.es/w.zs if c['source'] else 0])
        return im, ir, vd, vu

    def currents(self, x, c):
        _, _, vd, vu = self.circuit(x, c)
        return {'line': abs((vu-vd)/self.w.zl) if c['line'] else 0.,
                'source': abs((self.w.es-vu)/self.w.zs) if c['source'] else 0.}

    def rhs(self, t, x, c):
        w = self.w
        im, ir, _, _ = self.circuit(x, c)
        rr=self.rr0*(1+self.h.alpha_per_c*(x[-1]-self.h.reference_c))
        rs=self.rs0*(1+self.h.alpha_per_c*(x[-2]-self.h.reference_c))
        df = -rr*ir - 1j*w.wb*(1-x[2])*complex(x[0], x[1])
        torque = -3*w.wb*w.lm*(ir*np.conj(im)).imag/w.m.rated_va
        heat = ((abs(im)/w.ib)**2-x[3])/w.m.thermal.time_constant_s if w.m.thermal else 0.
        rates = [df.real, df.imag, (torque-w.load(x[2]))/(2*w.m.inertia_s), heat]
        currents = self.currents(x, c)
        for i, r in enumerate(self.relays):
            active, latched, available = c['relays'][r.id]
            rate = 0.
            if not latched:
                if not active:
                    rate = -x[4+i]/r.reset_s
                elif available:
                    multiple = currents[r.measurement_kind]/r.pickup
                    if multiple > 1:
                        if r.curve == 'definite':
                            rate = 1/r.time_multiplier_s
                        else:
                            k, alpha = {'standard_inverse': (.14, .02), 'very_inverse': (13.5, 1.), 'extremely_inverse': (80., 2.)}[r.curve]
                            rate = math.expm1(alpha*math.log(multiple))/(k*r.time_multiplier_s)
            rates.append(rate)
        h=self.h;ts,tr=x[-2:];cross=h.cross_w_per_k*(ts-tr)
        rates.extend([(3*rs*abs(im)**2-h.stator_ambient_w_per_k*(ts-h.ambient_c)-cross)/h.stator_capacity_j_per_k,
                      (3*rr*abs(ir)**2-h.rotor_ambient_w_per_k*(tr-h.ambient_c)+cross)/h.rotor_capacity_j_per_k])
        return np.array(rates)

    def initial_tail(self):
        return self.initial_temperatures

    def run(self, *, max_step=.001, rtol=2e-11, atol=2e-12):
        if max_step <= 0 or not 0 < rtol < 1 or not 0 < atol < 1:
            raise ValueError('positive bounded integration controls required')
        state, initial = self.w.initialize()
        if self.w.m.thermal and state[3] >= self.w.m.thermal.trip_level:
            raise ValueError('initial thermal state at or above trip threshold')
        state = np.r_[state, np.zeros(len(self.relays)),self.initial_tail()]
        c = dict(on=True, line=True, source=True, faults=set(), thermal=False,
                 available=True, since=0., failure_announced=set(), relays={r.id: [False, False, r.measurement_available] for r in self.relays})
        p = self.w.m.contactor
        if any(self.currents(state, c)[r.measurement_kind] >= r.pickup for r in self.relays if r.measurement_available):
            raise ValueError('initial inactive OC clock profile required')
        if p:
            c['available'] = abs(self.circuit(state, c)[2])/self.w.vb > p.dropout_voltage_pu
            if not c['available']:
                raise ValueError('initial available contactor profile required')
        queue = []
        events = []
        boundaries = []
        segments = []
        sequence = 0

        def log(t, kind, id, **kw):
            events.append(dict(time_s=float(t), kind=kind, equipment_id=id, **kw))

        def enqueue(t, kind, id, cause=''):
            nonlocal sequence
            sequence += 1
            heapq.heappush(queue, (float(t), 0 if kind == 'open' else 1, id, sequence, kind, cause))

        def request(t, id, cause):
            due = t+self.breakers[id].opening_delay_s
            log(t, 'breaker_open_requested', id, cause=cause, due_s=due)
            enqueue(due, 'open', id, cause)

        scheduled = []
        for f in self.w.s.faults:
            scheduled += [(f.apply_s, 'fault_apply', f.id), (f.clear_s, 'fault_clear', f.id)]
        scheduled += [(e.time_s, e.kind, e.equipment_id) for e in self.study.coupled.events]
        scheduled.sort()
        pos = 0

        def refresh(t, roots=()):
            currents = self.currents(state, c)
            for i, r in enumerate(self.relays):
                flags = c['relays'][r.id]
                threshold = r.pickup*(r.dropout_ratio if flags[0] else 1.)
                margin = currents[r.measurement_kind]-threshold if flags[2] else -threshold
                if ('pickup', r.id) in roots or (margin > 1e-9 if not flags[0] else margin < -1e-9):
                    flags[0] = not flags[0]
                    log(t, 'network_relay_pickup' if flags[0] else 'network_relay_dropout', r.id)
                if not flags[1] and (('trip', r.id) in roots or state[4+i] >= 1-1e-11):
                    flags[1] = True
                    state[4+i] = 1.
                    log(t, 'network_relay_trip', r.id)
                    request(t, r.breaker_id, 'relay:'+r.id)
            if p:
                v = abs(self.circuit(state, c)[2])/self.w.vb
                threshold = p.dropout_voltage_pu if c['available'] else p.pickup_voltage_pu
                if ('uv', self.w.m.id) in roots or (v <= threshold+1e-13 if c['available'] else v >= threshold-1e-13):
                    c['available'] = not c['available']
                    c['since'] = t
                    log(t, 'contactor_voltage_available' if c['available'] else 'contactor_voltage_unavailable', self.w.m.id)
            thermal = self.w.m.thermal
            if thermal and not c['thermal'] and (('thermal', self.w.m.id) in roots or state[3] >= thermal.trip_level):
                c['thermal'] = True
                c['on'] = False
                log(t, 'thermal_trip', self.w.m.id, outcome='latched_open')

        def uv_due():
            return c['since']+p.dropout_delay_s if p and c['on'] and not c['available'] and not c['thermal'] else math.inf

        t = 0.
        refresh(t)
        boundaries.append((t, state.copy(), copy.deepcopy(c), state.copy(), copy.deepcopy(c)))
        for batch in range(2048):
            if t >= self.w.s.duration_s-1e-14:
                break
            end = min(self.w.s.duration_s, scheduled[pos][0] if pos < len(scheduled) else math.inf,
                      queue[0][0] if queue else math.inf, uv_due())
            context = copy.deepcopy(c)
            guards = []
            keys = []

            def guard(key, fn, direction):
                fn.terminal = True
                fn.direction = direction
                keys.append(key)
                guards.append(fn)

            for i, r in enumerate(self.relays):
                flags = c['relays'][r.id]
                if flags[2]:
                    threshold = r.pickup*(r.dropout_ratio if flags[0] else 1.)
                    guard(('pickup', r.id), lambda t, x, r=r, q=threshold: self.currents(x, context)[r.measurement_kind]-q, -1 if flags[0] else 1)
                if not flags[1]:
                    guard(('trip', r.id), lambda t, x, i=i: x[4+i]-1, 1)
            if p:
                threshold = p.dropout_voltage_pu if c['available'] else p.pickup_voltage_pu
                guard(('uv', self.w.m.id), lambda t, x, q=threshold: abs(self.circuit(x, context)[2])/self.w.vb-q, -1 if c['available'] else 1)
            if self.w.m.thermal and not c['thermal']:
                guard(('thermal', self.w.m.id), lambda t, x: x[3]-self.w.m.thermal.trip_level, 1)
            roots = []
            if end > t+1e-14:
                solution = solve_ivp(lambda t, x: self.rhs(t, x, context), (t, end), state,
                                     method='DOP853', dense_output=True, events=guards,
                                     max_step=max_step, rtol=rtol, atol=atol)
                if not solution.success:
                    raise ValueError('independent integration failed: '+solution.message)
                stop = float(solution.t[-1])
                segments.append((t, stop, solution.sol, context))
                state = solution.y[:, -1].copy()
                roots = [k for k, hits in zip(keys, solution.t_events) if len(hits) and abs(hits[-1]-stop) < 1e-10]
                t = stop
            pre_state, pre_context = state.copy(), copy.deepcopy(c)
            before = len(events)
            refresh(t, roots)
            while pos < len(scheduled) and scheduled[pos][0] <= t+1e-12:
                _, kind, id = scheduled[pos]
                pos += 1
                if kind == 'fault_apply':
                    c['faults'].add(id)
                elif kind == 'fault_clear':
                    c['faults'].remove(id)
                else:
                    c['relays'][id][2] = kind == 'relay_measurement_restored'
                log(t, kind, id)
            refresh(t)
            while queue and queue[0][0] <= t+1e-12:
                _, _, id, _, kind, cause = heapq.heappop(queue)
                b = self.breakers[id]
                if kind == 'failure':
                    if id not in c['failure_announced'] and c[b.target_kind] and self.currents(state, c)[b.target_kind] > b.breaker_failure_current_a:
                        c['failure_announced'].add(id)
                        log(t, 'breaker_failure_confirmed', id)
                        for backup in sorted(b.backup_breaker_ids):
                            request(t, backup, 'breaker_failure:'+id)
                elif not c[b.target_kind]:
                    log(t, 'breaker_open', id, outcome='already_in_requested_state')
                elif b.fail_open:
                    log(t, 'breaker_open', id, outcome='failed_to_operate')
                    enqueue(t+b.breaker_failure_delay_s, 'failure', id)
                else:
                    c[b.target_kind] = False
                    log(t, 'breaker_open', id, outcome='applied')
                    refresh(t)
            if uv_due() <= t+1e-12:
                c['on'] = False
                log(t, 'motor_open', self.w.m.id, outcome='applied', cause='undervoltage_timer')
                refresh(t)
            if len(events) > before:
                boundaries.append((t, pre_state, pre_context, state.copy(), copy.deepcopy(c)))
            elif roots:
                raise ValueError('independent hybrid loop made no discrete progress')
        else:
            raise ValueError('independent event budget exceeded')
        self.segments, self.boundaries, self.events, self.initial = segments, boundaries, events, initial
        return self

    def state_at(self, time, side='regular'):
        boundary = next((b for b in self.boundaries if abs(time-b[0]) < 2e-12), None)
        if boundary:
            _, pre, pc, post, qc = boundary
            x, c = (pre, pc) if side == 'pre_event' else (post, qc)
        else:
            segment = next((s for s in self.segments if s[0]-1e-13 <= time <= s[1]+1e-13), None)
            if segment is None:
                raise ValueError('sample outside independent trajectory')
            x, c = segment[2](time), segment[3]
        return x,c

    def sample(self,time,side='regular'):
        x,c=self.state_at(time,side)
        im, ir, vd, vu = self.circuit(x, c)
        e = 1j*self.w.wb*self.w.lm/self.w.lr*complex(x[0], x[1])/self.w.vb
        values = dict(speed_pu=float(x[2]), thermal_level=float(x[3]), e_real_pu=float(e.real), e_imag_pu=float(e.imag),
                      current_pu=float(abs(im)/self.w.ib), bus_voltage_pu=float(abs(vd)/self.w.vb), connected=float(c['on']), thermal_latched=float(c['thermal']))
        channels = {self.w.m.id+'.'+k: v for k, v in values.items()}
        for i, r in enumerate(self.relays):
            flags = c['relays'][r.id]
            channels.update({r.id+'.'+k: float(v) for k, v in dict(progress=x[4+i], active=flags[0], latched=flags[1], measurement_valid=flags[2], measurement=self.currents(x, c)[r.measurement_kind] if flags[2] else 0.).items()})
        if self.w.m.contactor:
            channels[self.w.m.id+'.contactor_voltage_available'] = float(c['available'])
        for b in self.breakers.values():
            channels[b.id+'.closed'] = float(c[b.target_kind])
        for f in self.w.s.faults:
            channels[f.id+'.current_a'] = float(abs((vu if f.node_id == self.w.up else vd)/complex(f.r_ohm, f.x_ohm))) if f.id in c['faults'] else 0.
        h=self.h;ts,tr=x[-2:];rs=self.rs0*(1+h.alpha_per_c*(ts-h.reference_c));rr=self.rr0*(1+h.alpha_per_c*(tr-h.reference_c))
        zb=self.w.m.rated_ll_v**2/self.w.m.rated_va;cross=h.cross_w_per_k*(ts-tr)
        channels.update({self.w.m.id+'.'+k:float(v) for k,v in dict(stator_temperature_c=ts,rotor_temperature_c=tr,
            stator_resistance_pu=rs/zb,rotor_resistance_pu=rr/zb,stator_copper_loss_w=3*rs*abs(im)**2,rotor_copper_loss_w=3*rr*abs(ir)**2,
            stator_net_heat_w=3*rs*abs(im)**2-h.stator_ambient_w_per_k*(ts-h.ambient_c)-cross,
            rotor_net_heat_w=3*rr*abs(ir)**2-h.rotor_ambient_w_per_k*(tr-h.ambient_c)+cross).items()})
        return channels
