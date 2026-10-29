# CardiAtlas workspace in HeartTwin

HeartTwin provisions a persistent CardiAtlas SQLite file at `CARDIATLAS_DB`. On first access only, if that file has no records, relations, or claims, the adapter loads the packaged source-linked GSE132880 metadata reference bundle. Existing database content is never overwritten or reseeded.

The default Render configuration sets the database below `HEARTTWIN_WORKSPACE`, which is under the service's persistent `/var/data` disk. Do not point this path at an ephemeral container filesystem for production. A new blank environment bootstraps its initial reference corpus when an owner opens the CardiAtlas workspace or calls its health endpoint.

The bundled reference includes one mouse cardiac endothelial scRNA-seq study, its eight public GEO sample metadata records, one design claim with a source locator, and source-cited graph edges. It contains no expression matrices or personal patient data. The subject-label mapping is source-documented and is suitable for checking metadata and retrieval plumbing; domain review remains required before formal held-out-subject modelling. This bootstrap is not a clinical benchmark, and readiness is not clinical or causal validation.

The UI provides read-only research search, record/evidence inspection, claim inspection, graph-neighborhood exploration and dataset readiness. Editing claims or promoting releases is not exposed from the public UI. Those changes require the curator/CI process in CardiAtlas.

To use a separate prepared database, set `CARDIATLAS_DB` to its persistent path and restart HeartTwin. Existing non-empty stores are never automatically replaced.
