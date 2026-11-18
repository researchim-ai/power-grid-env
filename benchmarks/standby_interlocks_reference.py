"""Independent SI standby reference with initial PT masks and I2 close interlock.

The existing independent engine integrates current-square cooling after trip;
this adapter adds initial observation flags and checks persistent relay memory.
No native automation, current, thermal or return helpers are called.
"""
import math
from power_grid_env.study_spec import StudySpec
from benchmarks.automatic_return_reference import AutomaticReturnReference


class StandbyInterlocksReference(AutomaticReturnReference):
    def __init__(self, study):
        original = StudySpec.model_validate(study)
        p = original.coupled.network_automation.transfers[0]
        self.initial_pt = dict(primary=p.initial_primary_measurement_available,
                               reserve=p.initial_reserve_measurement_available)
        surrogate = original.model_dump(mode='json')
        entry = surrogate['coupled']['network_automation']['transfers'][0]
        for side in ('primary', 'reserve'):
            entry.pop('initial_'+side+'_measurement_available', None)
        surrogate['coupled']['events'] = [e for e in surrogate['coupled']['events'] if e['kind'] != 'ats_reset' and not e['kind'].startswith('transfer_')]
        super().__init__(surrogate)
        self.study = original
        self.pt_profile = not all(self.initial_pt.values()) or any(e.kind.startswith('transfer_') for e in original.coupled.events)

    def initial_context(self):
        c = super().initial_context()
        for side, valid in self.initial_pt.items():
            c[side+'_valid'] = valid
        return c

    def check_close(self, b, time, x, c):
        # Same physical precedence, independently evaluated from SI context.
        other = next(other for other in self.breakers.values() if other.id != b.id)
        if c[other.target_kind]:
            return dict(allowed=False, reason='interlocked_breaker_closed', interlock_id=other.id)
        if time-c['opened_at'].get(b.id, -math.inf) < b.minimum_open_s-1e-12:
            return dict(allowed=False, reason='minimum_open_time')
        for side in ('primary', 'reserve'):
            if not c[side+'_valid']:
                return dict(allowed=False, reason=side+'_measurement_unavailable')
        if b.target_kind == 'generator' and c['i2_latched']:
            return dict(allowed=False, reason='generator_thermal_latch')
        return super().check_close(b, time, x, c)
