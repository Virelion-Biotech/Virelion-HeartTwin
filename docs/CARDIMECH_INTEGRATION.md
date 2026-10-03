# CardiMech integration

HeartTwin integrates `Virelion-CardiMech` as a command service through `cardimech-hearttwin`.

## Capabilities

- `mechanics.health`
- `mechanics.backends`
- `mechanics.materials`
- `mechanics.simulate`
- `mechanics.prepare_calibration`
- `mechanics.validate.reference`
- `mechanics.ecosystem`

The adapter uses HeartTwin's standard `HEARTTWIN_CAPABILITY` plus JSON payload protocol. CardiMech owns mechanics contracts/backend selection; HeartTwin remains the cross-service orchestrator.

## Stack flow

```text
CardiAnatomy ---- mesh / fibres / regions ------┐
CardiEP -------- activation artifact -----------┤
MyoTrace/CMR --- mechanics observations --------┤
                                                v
                                           CardiMech
                                      / reference backend
                                     / spatial plugins
                                    v
                    PV / strain / stress / hemodynamics
                                    |
                      +-------------+-------------+
                      v                           v
                   HeartTwin                    CardiInfer
                   shared state          posterior calibration/UQ
```

`mechanics.prepare_calibration` creates a CardiInfer-compatible inverse problem. CardiInfer remains the inference engine; sampled parameters return through CardiMech's restricted forward envelope and may only update declared `passive.*`, `active.*`, or `circulation.*` namespaces.

## Reproducibility

`requirements-services.txt` pins CardiMech to commit `ceaa3b42d77fbdea4743ed27b8a29fa478d68a0b`.

The pinned revision is the merged, Python 3.10-3.12 CI-verified CardiMech 0.2.0 stack rather than an unmerged feature-branch SHA.\n\n## Scientific boundary

The built-in `numpy-lumped-v1` backend is a deterministic non-spatial software/reference model for integration, regression, fast sweeps, and inference plumbing. Spatial finite-element mechanics and empirical patient validation require explicit higher-fidelity backends and separate validation evidence.
