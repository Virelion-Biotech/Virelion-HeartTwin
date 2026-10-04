# Superstack continuity

HeartTwin's superstack workflow is the single-subject integration path that
composes the established multimodal and mechanistic workflows and then seals one
merged canonical `CardiacState` through CardiTrace.

The workflow is intentionally conservative.  A repository counts as connected
only when its output is either a typed canonical artifact, a provenance-linked
gate, or an explicit downstream parameter/control input.

Current causal/control links are:

- CardiStudio design -> benchmark-label coverage gate.
- DCCP selected host axis -> CardiAgent challenge severity.
- ElectroTrace calibration -> CardiInfer EP posterior -> explicitly mapped CardiEP parameter.
- MyoTrace dominant beat frequency -> CardiMech cycle length.
- OptiCell output -> imaging-QC gate before the mechanistic run.
- CardioScore output -> therapy-safety evidence gate and therapy-plan provenance.
- CardiAnatomy -> CardiEP -> CardiMech/CardiInfer -> CardiFlow -> CardiTherapy remains hash-linked.
- CardiBench -> CardiLearn -> CardiEval remains split/identity linked.
- CardiAgent -> CardiBridge -> CardiVex remains a real consumer handoff.
- CardiTrace independently recomputes the supplied canonical-state fingerprint.

No automatic transformation is made between unlike biological quantities.
Cross-domain parameter mappings must be supplied explicitly.
