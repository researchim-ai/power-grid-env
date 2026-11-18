# Controlled R(T) islands and checked reconnection

This bounded balanced radial profile combines a GENCLS generator, TGOV1/SEXS
limits, a two-cage motor, physical winding/generator heat, stator R(T), the I²
replica and shaft/kinetic/thermal energy with manual source/line switching.
It qualifies these combinations against independent SI equations; it does not
introduce a new general-purpose network model or certify real equipment.

```bash
python -m power_grid_env.cli run-study cases/topology-stator/source_cycle.json --out /tmp/source-cycle.json
python -m power_grid_env.cli run-study cases/topology-stator/line_cycle.json --out /tmp/line-cycle.json
python -m power_grid_env.cli run-study cases/topology-stator/dual_cycle.json --out /tmp/dual-cycle.json
python -m power_grid_env.cli run-study cases/topology-stator/source_blocked.json --out /tmp/source-blocked.json
python -m benchmarks.topology_stator_acceptance --out /tmp/topology-qualified
python -m benchmarks.topology_stator_verify --source /tmp/topology-qualified --out /tmp/topology-accepted
```

Use fresh output paths. Breaker requests are queued at 0.10 s and acted on
5 ms later. Generator/motor fluxes, rotor speed and physical temperatures remain
continuous, while algebraic voltages/currents may jump. A connected generator
feeds the motor island; it is not replaced by a fixed-frequency slack source.
Reference pulses at 0.05/0.15/0.25/0.35 s still exercise both controller bounds.
The I² threshold is raised to 100 to keep this topology corpus separate from
the [active protection race corpus](../network-stator/README.md).

- `source_cycle`: the source opens; the feeder remains connected to the island.
  A checked close at 0.145 s restores the external source.
- `line_cycle`: the feeder opens; the upstream bus stays at the external source
  voltage and the downstream island remains generator-fed. Checked closing
  restores the feeder at 0.145 s.
- `dual_cycle`: source and feeder open in the same event batch. The empty,
  disconnected upstream component has exactly zero voltage, no physical ground
  shunt and no invented losses. Source closing at 0.145 s first energizes that
  dead receiver. Feeder closing at 0.185 s then checks both live sides.
- `source_blocked`: closing is denied for excessive frequency difference. The
  source stays open, the island continues and the attempted close is counted.
  The manual opening inhibit remains set until a successful manual close.

All settings are authored numerical stress-test assumptions. The accepted
profiles deliberately use permissive synchronism windows (1 rad, 0.5 pu, 2 Hz);
the rejection case uses a 0.001 Hz window. These are **not operating settings**.
The external source is a fixed 380 V, 50 Hz equivalent. Machine/thermal
parameters retain the provenance and limitations of the
[stator R(T) profile](../generator-stator/README.md).

The independent reference forms its own five-unknown complex SI circuit and
differentiates `A(x) q = b(x)` as `A qdot = bdot - Adot q`. Terminal frequency
comes from `50 + Im(Vdot/V)/(2π)`, including motor flux, field and resistance
temperature changes. It is not assumed equal to rotor frequency. An empty
isolated coordinate is pinned to zero without adding admittance. Its frequency
is undefined (`null`), so energizing it follows the explicit dead-receiver rule.
Incoming supply checks precede that rule. No native stamps, synchronism, voltage
tangents, limiter or event helpers are used by the reference.

Ten cases cover four scenarios, passport/equilibrium temperatures and two
reversed command/relay/breaker lists. Radau/DAE on 2 ms and 1 ms grids give
**40 native runs, 20 independent trajectories and 80 comparisons**. The consumer
replays every reference and comparison, initial spectra, opening continuity,
source/line currents, empty components, attempt counters, manual inhibition,
eight permutation pairs and 40 event-split energy integrals. It checks
**56 openings, 48 successful closes, 8 blocked closes and 16 dead-receiver
energizations**. Synchronism reasons and every saved decision measurement are
compared, including the distinction between undefined and live frequency.

Observed maximum native energy-integral error is 0.01490 J (0.1 J gate),
independent dense error 2.65e-6 J (1e-4 J gate), and decision-frequency error
2.72e-9 Hz (5e-5 Hz gate). Numerical consistency is not equipment accuracy.

An accompanying analytic fix replaces the fixed generator impedance in inverse
relay current products with actual stator `Z(T)` and adds `-I dZ/Z`. Partial DAE
products use their supplied voltage, including inconsistent Newton iterates.
Tests difference all three inverse curves, temperature-only/mixed directions,
consistent/partial voltage and complex Schur products. An installed-wheel probe
reduces the temperature-partial error from 0.01384 A/K to 6.31e-11 A/K:

```bash
python -I benchmarks/stator_automation_probe.py --installed-dir /tmp/installed-runtime --study cases/network-stator/relay_first.json --out /tmp/stator-tangent.json
```

This derivative check does not qualify active inverse-relay trip trajectories
inside the new island corpus. Also open: active OC/UV combined with topology,
backup coordination, ATS, automatic reclose, multiple live islands, unbalanced
faults, PSS/GENROU, arbitrary simultaneous roots and public parameter anchors.
Installed-wheel replay is local on the same host with reference imports blocked;
no independent-host, authenticated/OEM delivery, GitHub or CI is claimed.
