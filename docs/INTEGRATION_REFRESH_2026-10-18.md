# Integration refresh and remaining scientific gates

## Repair and version qualification

HeartTwin previously sent `posterior_replay_selection` as a CardiMech numerical
setting. Current CardiMech correctly rejects unknown solver settings, causing
three live-stack workflow failures. HeartTwin now retains this workflow decision
under `provenance.hearttwin_orchestration`, adds it before computing the result
fingerprint, and leaves numerical settings strict. Backend output is copied;
a service cannot supply the reserved orchestration namespace. Posterior artifact
identity, the accepted parameter vector and parent-run lineage remain explicit.

The bootstrap script reads its service inventory from the compatibility lock;
its separate list previously omitted six mechanistic services. The moving-head
workflow now also runs on pull requests. The mechanistic-pipe canary is required
by the new frozen matrix, rather than being skipped outside moving-main CI.

`configs/compatibility-lock.json` and `requirements-services.txt` define matrix
v4 with 21 exact component commits. They were resolved from installed VCS
metadata rather than inferred from package version numbers. The final local
Python 3.12 suite passes 190 tests with one explicit upstream-fixture skip; all
22 registered services are available and pip reports no broken requirements. Tests, service
availability, observed package versions and source fingerprints are retained in
`validation/integration_refresh_2026_10_18`. Hosted frozen and moving-head
Python 3.10/3.11/3.12 checks are the acceptance checks for this refresh; their
results remain available through the pull request and workflow history.

The original v3 inputs are preserved unchanged under
`validation/compatibility_v3`. Their README identifies the historical HeartTwin
revision needed to reproduce that earlier state. The environment snapshot for
v4 records observed transitive versions; it is not a universal dependency lock.
Neither version qualification nor passing canaries is empirical twin validation.
The unavailable generated upstream CDT fixture remains an explicit skipped gate.

## Other completed checks

- MyoTrace: all three independently fetched movies match the original size/hash
  pins. Atomic acquisition and corrupt-cache handling are covered by regression
  tests. All 12 public movie/method combinations execute and exactly repeat.
  The 20kPa motion-method disagreement remains visible; there is no independent
  beat/force truth. [Evidence](https://github.com/Virelion-Biotech/Virelion-MyoTrace/blob/66a512d8ef7fc0c981d3d5bb804ff99528797ed3/validation/maintenance_2026_10_18/REVIEW.md).
- CardiSim: transient HTTP inventory failures retry within a bounded budget;
  a fresh query returned the 12 artifacts in Zenodo record 17651628 revision 4.
  No surrogate training or patient validation was performed.
  [Evidence](https://github.com/Virelion-Biotech/Virelion-CardiSim/blob/main/docs/MAINTENANCE_2026-10-18.md).
- Intelligence: the repaired scanner passes the real Git checkout while still
  rejecting application logs/.env files. A fresh seven-day live run completed
  with 35 adapter successes, no adapter failures/errors and a passing report
  integrity check. Candidate extraction accuracy remains unreviewed.
  [Evidence](https://github.com/Virelion-Biotech/Virelion-Intelligence/blob/main/validation/maintenance_2026_10_18/LIVE_EXECUTION.json).
- CardiInfer: another unchanged local CPU execution passed 17 numerical
  experiments. It is not an observed Colab run or empirical uncertainty
  calibration. The historical failed direct-rejection coverage result remains.
  [Evidence](https://github.com/Virelion-Biotech/Virelion-CardiInfer/blob/9aabf87bdbee4c70b98229baa3bbea3fb896b490/validation/maintenance_2026_10_18/REVIEW.md).

## Resource/data gates left open

CardiLearn's existing resolver completed the exact mappings for all 33 samples
in the four raw-count rescue cohorts: 33 runs, 51 FASTQ files, 321,220,239,472
compressed bytes. GSE52313 alone accounts for 265,623,335,813 bytes. The available
runtime had approximately 27 GB free disk, an 8 GiB memory limit and no
STAR/fastp/featureCounts installation. Read download, alignment and baseline
training were not started; no baseline result or stage advancement is claimed.
The resolved manifest and resource report were delivered separately. Provision
a suitable host for the existing count-rescue workflow; reference indexes and
intermediate files require capacity beyond the compressed inputs.

The supplied ARGO v2 recovery ZIP failed during environment setup before record
validation. Main already contained the v3 ensurepip-free bootstrap repair when
this work started, so that fix was preserved. Full 1,962-record/nine-patient
readiness, coordinate-unit reconciliation, independent electrical truth and
measured cross-domain/outcome qualification remain open. No synthetic benchmark,
metadata lock or successful download closes those scientific gates.
