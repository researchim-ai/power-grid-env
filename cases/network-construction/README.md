# Construct a radial network, select equipment and verify the design

This profile extends single-feeder sizing into owner-constrained network
construction. A proposal chooses a transformer, permitted corridors and a cable
for each selected corridor. The compiler creates actual lines and terminals,
then runs every required motor study on three grids.

```bash
python -m power_grid_env.cli compile-network-design --input cases/network-construction/task-2-motors.json --proposal cases/network-construction/proposal-chain.json --out /tmp/compiled-network.json
python -m power_grid_env.cli network-design-view --input cases/network-construction/task-2-motors.json --proposal cases/network-construction/proposal-chain.json --out /tmp/network-view
python -m power_grid_env.cli network-design --input cases/network-construction/task-2-motors.json --proposal cases/network-construction/proposal-chain.json --out /tmp/network-design
python -m power_grid_env.cli verify-network-design /tmp/network-design --input cases/network-construction/task-2-motors.json --proposal cases/network-construction/proposal-chain.json
```

Use fresh paths. Accepted studies return exit code 0. Rejected, incomplete and
unqualified studies return 2 with distinct JSON status/reward fields. A complete
static rejection still runs all mandatory dynamics. Incomplete or numerically
unqualified studies never receive a positive reward or a qualified rejection.

The two example tasks have two/three 75 kVA single-cage motors at fixed 400 V
load sites, plus the existing auxiliary load. Owner demand envelopes cover all
motor nameplates and auxiliary apparent power. Three scenarios are mandatory:
simultaneous start, staged start and source-loss/restart. All run for 4 s on
2/1/0.5 ms grids with explicit motor and voltage criteria.

Proposals:

- `proposal-star.json`: separate short feeders for the two-motor task.
- `proposal-chain.json`: shared trunk then a short connection between sites.
  The upstream cable carries both downstream demand envelopes.
- `proposal-long.json`: a 600 m permitted corridor; it fails the owner's drop
  requirement and is retained as a verified rejected design.
- `proposal-three-star.json`: three feeders for the three-motor task.

The proposal contains only `transformer_id` and selected `corridor_id/cable_id`
pairs. The owner fixes sites, loads, motor passports, demand envelopes, allowed
routes, installation conditions, disturbances, criteria and catalog facts.
Candidates cannot add arbitrary nodes, delete a load or change acceptance
thresholds. Selected corridors must form a spanning tree over every owner node.
Unknown catalog IDs, loops, disconnected sites, invalid references and generated
ID collisions fail before output creation. Equivalent branch order compiles
identically. The full dynamic scope is preflighted at all three output grids.
For agent scoring, the host must retain the trusted owner task and submitted
proposal and supply both anchors to verification. The CLI and local manifests
are not a filesystem sandbox or source authentication.

## Equipment and calculations

The existing [catalog](../equipment-catalog/catalog.json) supplies offline
Nexans cable and historical Trihal transformer extracts with field-level
document references. Both the old single-feeder compiler and new constructor
use the same data-only passport derivation. The catalog's AC/DC resistance,
generic reactance and transformer short-time assumptions remain explicit;
they have not become authenticated manufacturer ratings.

Static screening includes downstream current aggregation, derated ampacity,
transformer demand, fixed-nominal-current projected voltage drop **including
the transformer**, cable adiabatic I2t, supplied interrupting rating and
transformer short-time limits. Fault current follows the complete source-to-node
impedance path. Cable duty uses its upstream end, which is conservative for
the declared passive positive-R/X source-only radial model. Voltage-drop
screening is linear and is not a constant-power load flow; dynamic voltage
windows are checked separately by the actual RMS network calculation.

The transformer is a secondary-referred series Thevenin equivalent under a
stiff primary. This profile excludes transformer magnetizing/inrush dynamics,
motor fault contribution, peak making duty, earth faults, protection selectivity,
multiple sources, meshed proposals, multiple voltage levels and normative approval.
Installation support remains the catalog's exact tabulated single-circuit air
conditions; no interpolation or inferred grouping compatibility is introduced.

The API bounds proposals to 2–8 fixed nodes, one source, at most eight single-cage
motors and eight studies, subject to the existing numerical/resource budgets.
The measured qualification here is narrower: **3/4 nodes, 2/3 motors, 4 s**.

## Public visual observations

`network-design-view` exports `single-line.svg`, `observation.json` and a sealed
receipt. The drawing shows the proposed transformer, branches, cable IDs,
lengths, motor IDs and owner demand sites. It contains no feasibility verdict,
reward, simulated future trajectory or event schedule. Labels are escaped;
long labels retain their full value in SVG titles. Layout is deterministic.
The benchmark rasterizes the exact SVGs through system librsvg/Cairo for QA.
These are public proposed single-line observations, not a trained VLM or an
image-grounding evaluation. Waveforms/passport images remain subsequent work.

## Reproduce qualification

```bash
python -m benchmarks.network_construction_acceptance --out /tmp/construction-qualified
python -m benchmarks.network_construction_acceptance --source /tmp/construction-qualified --out /tmp/construction-accepted
python -m benchmarks.network_construction_visual_check --source /tmp/construction-qualified --out /tmp/construction-visual-qa
```

Five designs exercise short star/chain layouts and long-route rejection on
both site sizes: **45 mandatory dynamic grid runs**, with 3 accepted and 2
rejected designs. Independent SI nodal equations check projected voltage drop
and shared currents. **17 OpenDSS fault solutions** check every node; emitted
network impedances and endpoints are also compared against independently
derived catalog parameters. The separate consumer recomputes engineering
audits, owner/proposal anchors, static references and public drawings.
OpenDSS here validates passive steady faults, not motor dynamics.

The previous six-family feeder corpus is rebuilt under this runtime: **108
grid runs**, including qualified infeasible choices. It enables all corpus
regressions without skipping them. The selected regression has **157 passing
tests, including 32 new construction/view tests**, with no failures or skips.

Installed-wheel replay uses Python-I with reference imports blocked: **54
studies** (45 new +9 legacy), exact event/axis/common-channel comparison.
Six-record batches are separately sealed sibling artifacts. A composite receipt
anchors them without exceeding the unchanged 256 MB per-artifact limit; the
receipt alone is not a self-contained archive of all batch data. Checks are on
the same host, not authenticated external delivery. No GitHub or CI is used.
