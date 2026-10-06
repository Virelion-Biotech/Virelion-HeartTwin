# Optical stimulation compatibility

HeartTwin supports a deliberately small optical-stimulation compatibility layer for cardiac research. This is an interface extension, not a new Virelion product line.

## Shared contract

The canonical metadata shape is `schemas/optical-stimulation-1.0.0.schema.json` with schema version `virelion.optical-stimulation/1.0.0`. HeartTwin exposes it through the optional `optogenetic_stimulation` field in `cardiac-twin-input-1.0.0.schema.json`.

The object carries only what downstream components need to agree on: protocol identity, modality, target, actuator, wavelength/irradiance/pulse timing, open- or closed-loop control metadata, and provenance.

## Puzzle-piece flow

1. **CardiAtlas** indexes cardiac optical-stimulation evidence and source identifiers.
2. **HeartTwin** carries the shared protocol object without changing the canonical cardiac-state contract or forcing optical stimulation on existing workflows.
3. **OptiCell** preserves the same object alongside imaging analysis so image-derived measurements remain tied to the perturbation that generated them.
4. **CardiSim** consumes the same timing/light fields and provides an explicit current hook. The simulator does not invent opsin kinetics: a validated or externally supplied open fraction is required before a conductance current is calculated.
5. A future **CardiEval** benchmark can compare measured and simulated responses using the protocol ID as the join key. No CardiEval implementation is added by this compatibility change because no locked optical benchmark has yet been established.

Conceptually:

`CardiAtlas evidence -> HeartTwin protocol -> CardiSim perturbation -> experimental protocol -> OptiCell measurement -> HeartTwin result -> future CardiEval comparison`

## Scope boundary

This layer does **not** add opsin engineering, viral-vector development, implantable optical hardware, a therapeutic optogenetics program, or claims of a validated biophysical opsin model. It also does not make optogenetics a required HeartTwin modality.

CardiSim's optical current helper is intentionally a generic conductance interface: `I_opsin = g_max * open_fraction * (V - E_rev)`. `open_fraction` must come from an explicitly selected and separately validated kinetics model or experimental estimate. The light schedule helper only tells callers when illumination is active.

## Why the contract also permits optoelectronic stimulation

The field is named `optogenetic_stimulation` for compatibility with the initial HeartTwin integration request, but the shared schema permits `modality: optoelectronic` as well. That keeps the interface usable for non-genetic photostimulation platforms without creating another parallel contract.

## Versioning rule

Changes that add optional metadata may remain backward compatible. Any change to units, required fields, semantics, or numerical meaning requires a new `virelion.optical-stimulation/*` schema version. Existing cardiac-state consumers should remain unaffected.
