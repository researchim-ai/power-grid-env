"""Published Aalto windings; authored relay, snubber and mechanism acceptance."""
import math
from power_grid_env.motor_catalog import CATALOG_IDS,catalog_entry
from power_grid_env.phase_network import PhaseCase
from power_grid_env.protected_motor import ProtectedMotorStudy

NAMES=tuple(k+'__'+mode for k in CATALOG_IDS for mode in ('restart','locked'))+('aalto-saturated-viscous-thermal__temperature','aalto-saturated-fan__cool_reclose')


def case(name):
    if name not in NAMES:raise ValueError('registered protected motor case required')
    from power_grid_env.motor_catalog import protected_catalog_study
    identifier,mode=name.split('__');return protected_catalog_study(identifier,mode)
