# Cardiac-state crosswalk review

HeartTwin executes only direct identity or sign-inversion mappings between CardiSim normalized phenotypes and CardiVex burden domains. The executable crosswalk is versioned as `0.1.0`.

Safe mappings are contractility→contractile impairment, electrophysiology→electrophysiologic disturbance, metabolism→metabolic stress, mitochondrial health→mitochondrial dysfunction, viability→viability burden, plus identity mappings for inflammation, fibrosis, and oxidative stress. CardiSim angiogenesis and hypertrophy are intentionally not auto-mapped. Ischemic burden remains unresolved. Downstream scenarios label these transforms as extrapolated rather than empirical patient state.
