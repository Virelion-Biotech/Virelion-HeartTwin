# Regenerative evidence workspace integration

HeartTwin's therapeutic workspace is a read-model boundary over component outputs, not a new evidence source. It accepts already-curated CardiAtlas records, CardiBench stratum records, CardiEval's independent regeneration dimensions, MyoTrace maturity profiles, ElectroTrace genotype/context metadata, and CardiSim synthetic challenges. It does not scrape sources or infer missing fields.

## Boundaries

- Evidence state is explicit: observed, curated, synthetic or simulated. These labels remain visible in the returned projection.
- Four therapeutic strata remain separate: myocardial regeneration, myocardial repair/preservation, genetic disease modification and vascular tissue engineering.
- CardiEval dimensions remain independent: cell-cycle entry, mitosis/cytokinesis, lineage/cell number, functional maturation, ventricular function/remodelling, fibrosis/scar, arrhythmia safety and off-target proliferation/toxicity. No default composite score is computed.
- MyoTrace modality-specific missingness is preserved; a missing maturity dimension is not zero.
- Genotype is only accepted as confirmed/reported with source record IDs, or unknown. HeartTwin never infers a genotype from phenotype.
- Synthetic challenge records require a seed and generator version and remain labelled synthetic/simulated.
- Virtual interventions are rejected unless CardiTherapy explicitly reports a supported backend; regenerative intervention execution additionally requires explicit authorization.

## API

`build_therapeutic_workspace(...)` validates component outputs and produces a deterministic-shape JSON projection for a workspace UI. It does not claim the data have been independently scientifically validated. The current contract is additive and does not modify canonical CardiacState 1.4.0 or the frozen compatibility matrix.

## Compatibility snapshot

See `docs/REGENERATIVE_EVIDENCE_COMPATIBILITY_V1.json` for the exact main-branch commit and package version pins used to define the source contract snapshot. These are source snapshots, not proof that the new component PRs have been merged or that their APIs are already mutually qualified.
