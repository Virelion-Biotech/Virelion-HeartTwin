# Therapeutic workspace component adapters

This document describes the explicit conversion boundary in
`hearttwin.therapeutic_workspace`. These functions convert already-produced
component outputs; they do not fetch component services, infer missing evidence,
or establish clinical validity.

## Supported conversions

| Source component | Adapter | Conversion |
|---|---|---|
| CardiAtlas | `adapt_cardi_atlas_record` | Maps repair and preservation to the benchmark's combined repair/preservation stratum; keeps immune modulation separate. Unknown classes fail closed. The caller must supply the evidence state, component version, and source provenance. |
| CardiEval | `adapt_cardi_eval_assessment` | Maps CardiEval's cardiomyocyte-number/lineage and off-target-toxicity domain keys to HeartTwin's stable names. All eight domains are required; duplicates and unknown domains fail closed. |
| ElectroTrace | `adapt_electrotrace_genotype_profile` | Extracts the supplied nested genotype context. Reported/curated assertions require source IDs and complete source provenance. |
| CardiEP | `adapt_cardiep_disease_context` | Maps `genotype_status` to the shared `status` field without changing variant assertions. ECG-inferred genotype is rejected. |
| CardiSim | `adapt_cardisim_scenario` | Promotes the scenario's manifest evidence state, seed, and generator version into the workspace challenge envelope while retaining axes and fingerprint. |
| MyoTrace | Native profile accepted | Maturity modality is validated per dimension. A global modality is deliberately not required because one profile can contain measurements from multiple modalities. Missing dimensions remain missing. |

## Evidence-state and provenance rules

- Observed and curated records require source record IDs and a source provenance
  entry with source identifier, source type, URL, and SHA-256 digest. A
  `canonical_source_url` is accepted for CardiTrace-style provenance.
- Synthetic/simulated challenges require both a seed and generator version.
- The adapter never converts a missing value into a negative result or zero.
- A component's source classification is not silently promoted to a stronger
  verification state. The caller explicitly supplies the evidence state where
  a source component does not carry a compatible field.
- The workspace is a read model only. Live service refresh, browser UI, and
  regenerative execution are not implemented by these adapters.

## Compatibility note

These adapters are a versioned integration seam, not a guarantee that every
component PR has been merged or that all component services expose a live API.
Re-pin and re-run the full integration matrix after upstream PRs are merged.
