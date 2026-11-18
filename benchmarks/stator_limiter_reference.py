"""Independent SI stator/heat/energy equations plus root-localized limit modes."""
import copy
from power_grid_env.study_spec import StudySpec
from benchmarks.generator_stator_reference import GeneratorStatorReference
from benchmarks.limiter_winding_reference import LimiterReference


class StatorLimiterReference(GeneratorStatorReference):
    def __init__(self,study):
        original=StudySpec.model_validate(study);surrogate=copy.deepcopy(study);surrogate['coupled']['events']=[]
        super().__init__(surrogate);self.study=original
        if original.coupled.faults or original.coupled.generator_thermal or original.coupled.duration_s>2. or len(original.coupled.motors)!=1 or original.coupled.motors[0].contactor or any(r.pickup<1e8 for r in self.relays):raise ValueError('bounded stator controller pulses with inactive protection required')
        if any(e.kind not in {'governor_step','voltage_reference_step'} or e.equipment_id!=self.g.id for e in original.coupled.events):raise ValueError('only generator reference pulses supported')
        self.entries={}
        if self.gov_address is not None:self.entries[self.gov_address]=('TGOV1',self.gp.governor.VMIN,self.gp.governor.VMAX)
        if self.avr_address is not None:self.entries[self.avr_address+1]=('SEXS',self.gp.exciter.EMIN,self.gp.exciter.EMAX)

    def control_values(self,x,c,*,free=False):return LimiterReference.control_values(self,x,c,free=free)

    def run(self,**kwargs):return LimiterReference.run(self,**kwargs)
