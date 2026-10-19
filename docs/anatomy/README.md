# Anatomical heart reference and attribution

BodyParts3D, © The Database Center for Life Science licensed under CC Attribution 4.0 International.

The default HeartTwin model is now a real anatomical reference surface derived from
**BodyParts3D 4.0**, an adult human male atlas. It replaces the procedural schematic
as the default display. This reference is a static visualization asset, not a patient
reconstruction or a simulation-ready tetrahedral volume mesh.

- Original dataset: https://dbarchive.biosciencedbc.jp/en/bodyparts3d/download.html
- Official license, updated 2025-02-27: https://dbarchive.biosciencedbc.jp/en/bodyparts3d/lic.html
- License text: https://creativecommons.org/licenses/by/4.0/
- Original publication: Mitsuhashi et al., *BodyParts3D: 3D structure database for
  anatomical concepts*, https://doi.org/10.1093/nar/gkn613
- Curated heart conversion by OrganUI: https://github.com/organui/heart-3d
- Exact input GLB SHA-256:
  `b80a727d022a4ab07ccfcf15caccc228eaccb6d69ec0e72923324946ac35a946`

The OrganUI conversion selected and grouped source meshes from the official IS-A
archive and source ontology mappings. Its upstream attribution, provenance and complete
source-element checksum manifest are preserved here as historical records. Relative
links in those verbatim records refer to the upstream repository. The original OBJ
headers cite an older CC BY-SA 2.1 Japan notice; the current official archive explicitly
grants CC BY 4.0, which is the distribution basis recorded by OrganUI and used here.
The application code's AGPL license does not replace the asset's CC BY 4.0 license.

HeartTwin changes: restored millimetre dimensions by reversing OrganUI's 0.02 display
scale, retaining the recentered `[x,z,-y]` axes; simplified each structure with quadric
mesh decimation; rounded coordinates; assigned illustrative colors; converted the model
to compact JSON and gzip/base64 encoding. No heart anatomy was procedurally invented
for this reference. The exact before/after counts and source IDs are in
[reference-manifest.json](reference-manifest.json).

There are 11 selectable groups: right and left atrial walls; one combined ventricular
wall; right and left ventricular cavity surfaces; ascending aorta; aortic arch;
pulmonary trunk; superior vena cava; and left and right coronary artery trees. Cavity
surfaces are hidden initially because they represent spaces, not myocardial tissue.
The ventricular wall is not falsely split into independently segmented left/right walls.
Some anatomy is omitted and vessel ends are truncated. Colors are illustrative.
Independent clinical anatomy review of this display derivative has not been performed.

The bundled display derivative has 13,809 vertices and 22,859 triangular surface faces.
Decimation is for browser display only and is not applied to uploaded research meshes.
No activation, electrical recordings, pumping motion or physiological parameters are
bundled with this asset.

## Reproduce

Use Python 3.12 with NumPy and `fast-simplification==0.2.0`. Download the input GLB from
the URL recorded in the manifest, then run from the repository root:

```bash
python scripts/build_reference_heart.py /path/to/heart.glb
node --test tests/webui_geometry.test.cjs
```

The converter refuses an input checksum mismatch. The generated asset contains a
SHA-256 of the uncompressed geometry, checked in the browser before parsing. The
Node tests also verify the distribution file's checksum against the separate manifest,
source mapping, geometry contracts and default cavity visibility. No runtime dependency
on the upstream data host or a compute worker is required.
