# Cardiac-state crosswalk review

Status: **direct identity/sign-inversion subset implemented; ambiguous mappings still require biological sign-off**.

HeartTwin now has typed orchestration contracts, but CardiVex, CardiSim, and DCCP still use distinct scientific vocabularies. This document makes the proposed mappings explicit so no service silently reinterprets another service's state.

## Executable safe subset

HeartTwin `state_crosswalk.py` version `0.1.0` currently executes only mappings that are direct semantic identities or sign inversions:

- CardiSim `contractility` → CardiVex `contractile_impairment = 1 - contractility`
- `electrophysiology` → `electrophysiologic_disturbance = 1 - electrophysiology`
- `metabolism` → `metabolic_stress = 1 - metabolism`
- `mitochondrial_health` → `mitochondrial_dysfunction = 1 - mitochondrial_health`
- `viability` → `viability_burden = 1 - viability`
- `inflammation` → `inflammatory_activation`
- `fibrosis` → `fibrosis_remodeling`
- `oxidative_stress` → `oxidative_stress`

CardiSim `angiogenesis` and `hypertrophy` are intentionally not converted into vascular or structural-dysfunction domains.

## Canonical candidate

CardiVex's empirically exercised domain/state vocabulary is the current candidate canonical representation for downstream defensive evaluation. This is a proposal, not an automatic scientific equivalence claim.

## Proposed mappings

| CardiVex concept | CardiSim candidate | DCCP candidate | Confidence | Disposition |
| --- | --- | --- | --- | --- |
| contractility / contractile impairment | contractility | contractile_functional | high | eligible for domain review |
| electrophysiologic disturbance | electrophysiology | contractile_functional (partial) | medium | requires review; DCCP axis is broader |
| endothelial / vascular dysfunction | angiogenesis | vascular_endothelial | medium | requires review; angiogenesis is not equivalent to dysfunction |
| metabolic stress | metabolism | metabolic_mitochondrial | high | eligible for domain review |
| mitochondrial dysfunction | mitochondrial_health | metabolic_mitochondrial | high | eligible for domain review |
| oxidative stress | oxidative_stress | metabolic_mitochondrial | medium-high | requires weighting review |
| inflammatory activation | inflammation | inflammatory | high | eligible for domain review |
| structural disorganization / fibrosis remodeling | fibrosis, hypertrophy | structural_injury, remodeling | medium-high | requires aggregation review |
| viability burden / tissue injury | viability | cell_death, structural_injury | medium | requires direction/sign convention review |
| ischemic burden | no single direct dimension | no single direct axis | low | **unresolved; must not be auto-mapped** |

## Rules

1. Raw service outputs remain preserved alongside every canonical transform.
2. Every executable crosswalk must carry a version and transformation provenance.
3. Low-confidence mappings may not be introduced as defaults.
4. `ischemic_burden` remains unresolved until a domain reviewer defines and validates a composite or direct observable.
5. Round-trip tests are required only where the mapping is mathematically invertible; lossy mappings must be labeled lossy.
6. No HeartTwin state may be promoted to `externally_validated` merely because a vocabulary transform succeeds.

## Engineering follow-up after scientific sign-off

Once the mappings above are ratified, implement them in a small versioned crosswalk package rather than copying dictionaries among CardiSim, DCCP, CardiVex, and HeartTwin. HeartTwin should store both the original service artifact and the canonicalized representation with transformation provenance.
