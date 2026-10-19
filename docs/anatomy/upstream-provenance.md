# Model provenance and preparation

## Exact source and terms

The runtime model is a curated subset of **BodyParts3D 4.0**, release dated **2013-05-16**, obtained directly from the [official LSDB archive](https://dbarchive.biosciencedbc.jp/en/bodyparts3d/download.html) on **2026-09-22**. The geometry archive is `isa_BP3D_4.0_obj_99.zip`, labeled “polygon reduction rate = 99% IS-A Tree”. “99%” is the archive's reduction label, not an OrganUI accuracy claim.

The [official license page](https://dbarchive.biosciencedbc.jp/en/bodyparts3d/lic.html), last updated **2025-02-27**, specifies [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/). It explicitly permits redistribution and derivative works with attribution. Required credit:

> BodyParts3D, © The Database Center for Life Science licensed under CC Attribution 4.0 International.

The downloaded OBJ comments retain an older **CC BY-SA 2.1 Japan** notice and link to that same license page. This discrepancy is explicit: we rely on the current grant from the official archive for assets downloaded there, not on older downstream copies. A byte-for-byte [license-page snapshot](model-source/archive-license.html.txt) is preserved as text; it is not served as executable HTML. The MIT code license does not cover the model.

Authoritative sources:

- [Archive download page](https://dbarchive.biosciencedbc.jp/en/bodyparts3d/download.html)
- [Release 4.0 notes and coordinate diagram](https://dbarchive.biosciencedbc.jp/data/bodyparts3d/LATEST/release_4.0.html)
- [Archive database description](https://dbarchive.biosciencedbc.jp/en/bodyparts3d/desc.html)
- Mitsuhashi et al. (2009), [doi:10.1093/nar/gkn613](https://doi.org/10.1093/nar/gkn613)

## What the source actually contains

Geometry inspection changed the initial selection. The PART-OF archive and compound concepts `FMA9533`/`FMA9556` do **not** supply intact, independently selectable right and left ventricular walls. Their mapped elements are mainly papillary muscles and a shared valve leaflet. Using them as complete ventricular walls produced a visibly incomplete heart.

The IS-A archive includes **FJ2428**, mapped to **FMA13884 — wall of ventricle**, which supplies the combined ventricular wall. The app keeps it combined. It does not split that mesh or invent a left/right tissue boundary. The separately mapped ventricular cavities are included for internal exploration; they are labeled as solid representations of chamber space, not tissue.

| Display group | Source concept | Source geometry / hierarchy |
| --- | --- | --- |
| Right atrium | FMA9457, wall of right atrium | FJ2439 / PART-OF |
| Left atrium | FMA9531, wall of left atrium | FJ2438 / PART-OF |
| Ventricular wall | FMA13884, wall of ventricle | FJ2428 / IS-A |
| Right ventricular cavity | FMA9291 | FJ2423 / PART-OF |
| Left ventricular cavity | FMA9466 | FJ2422 / PART-OF |
| Ascending aorta | FMA3736 | FJ3413 / PART-OF |
| Aortic arch | FMA3768 | FJ3411 / PART-OF |
| Pulmonary trunk | FMA8612 | FJ2966 / PART-OF |
| Superior vena cava | FMA4720 | FJ3645 / PART-OF |
| Left coronary artery tree | FMA50040 | All elements mapped to this concept / PART-OF |
| Right coronary artery tree | FMA50039 | All elements mapped to this concept / PART-OF |

Every element is assigned once. The exact tables are preserved in [selected-mappings.tsv](model-source/selected-mappings.tsv), and every original OBJ has its own checksum, vertex count, and triangle count in [manifest.json](../public/models/manifest.json). The manifest also records full input and runtime hashes. These are source mappings, not a claim of expert anatomical review.

Omitted: independently selectable valves, papillary muscles, atrial cavities, pulmonary veins, the inferior vena cava, most other vessel segments, pericardium, and surrounding organs. Vessel ends are visibly truncated. Coronary trees are selectable groups, not individually named branches. The source model represents an adult male; it is not a universal patient reference. The [release notes](https://dbarchive.biosciencedbc.jp/data/bodyparts3d/LATEST/release_4.0.html) caution about missing and incorrect anatomical representations.

## Reproduce the runtime asset

The small runtime `public/models/heart.glb` is included in the checkout. Ordinary setup does not download the raw archive. To regenerate it:

```sh
bun install --frozen-lockfile
bun run prepare:model
bun run verify:model
```

Run these from this repository. The script downloads pinned input URLs from [model-inputs.json](../scripts/model-inputs.json) only when their local cache files are absent. The large archive and raw tables stay in ignored `.asset-cache/`. It verifies SHA-256 **before** conversion and refuses upstream changes. `LATEST` URLs are therefore pinned by content, not trusted to remain unchanged. If the archive changes, review the new data and license before updating hashes.

Preparation uses Bun, fflate, Three.js, and glTF Transform; their exact versions are locked. Steps:

1. Read selected FMA concepts from the source hierarchy tables and resolve their OBJ element IDs.
2. Extract only those elements from the official IS-A archive. Reject missing mappings or duplicate mesh ownership.
3. Parse OBJ vertices and faces; triangulate polygons with a fan and retain source topology.
4. Center the entire selection on its source bounding box; transform `(x,y,z)` to `(x,z,-y)` and multiply coordinates by `0.02`. This is an arbitrary display scale, not a measurement calibration. The exact center/bounds are in the manifest.
5. Recompute vertex normals and assign illustrative colors. No sculpting, texture generation, additional decimation, or simulated movement.
6. Merge elements within each selectable concept into an indexed mesh. Store the stable structure ID and FMA ID on each GLB node.
7. Export GLB and write the complete input/element/output manifest.

`bun run verify:model` independently decodes the runtime, verifies its checksum, checks every expected structure/FMA pair, ensures geometry is finite and indices are valid, and reports the actual geometry counts.

## Anatomy text and review

Descriptions are original concise summaries of the US National Heart, Lung, and Blood Institute's [blood-flow reference](https://www.nhlbi.nih.gov/health/heart/blood-flow) and [heart anatomy reference](https://www.nhlbi.nih.gov/health/heart/anatomy), checked on 2026-09-22. No diagrams, videos, or third-party medical illustrations from those pages are redistributed. In-app reference links appear under each structure's notes.

Completed: source identifier checks, mesh inspection, and interface verification. **Not completed: review by a clinical anatomy expert, independent clinical validation, or physiological validation.** The app, README, and model dialog disclose this. The guided tour explains static spatial relationships; it does not animate or simulate circulation. Pathology, treatment, measurement, and heartbeat simulation are outside scope.
