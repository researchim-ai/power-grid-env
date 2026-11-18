"""Independent acknowledgement of a dimensionless I2 replica; no heat reset."""
import math
from benchmarks.standby_interlocks_reference import StandbyInterlocksReference


class ThermalRecoveryReference(StandbyInterlocksReference):
    extra_event_kinds=frozenset({'generator_thermal_reset','breaker_open'})

    def after_thermal_trip(self,time,state,context):
        context['opened_at']['reserve-cb']=float(time)

    def extra_command(self,event,time,state,context):
        if event.kind!='generator_thermal_reset':return None
        p=self.replica;level=float(state[self.replica_address])
        result=dict(level=level,threshold=p.reset_level)
        if p.reset_level is None:return result|dict(outcome='blocked',reason='thermal_reset_disabled')
        if context['generator_on']:return result|dict(outcome='blocked',reason='generator_connected')
        if not math.isfinite(level) or level>p.reset_level:return result|dict(outcome='blocked',reason='thermal_level_above_reset')
        if not context['i2_latched']:return result|dict(outcome='already_in_requested_state')
        context['i2_latched']=False
        return result|dict(outcome='applied')
