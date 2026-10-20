# Patient lifecycle Colab evidence — 2026-10-20

The final validation was executed in Google Colab through the user's connected Edge browser against HeartTwin commit `d5bb8ac6928b732561944c746d4d199b7581dc2b` and six explicitly pinned component revisions.

**Final result: 95 tests passed; zero failures, errors or skips.** Python compilation, patient lint, JavaScript syntax and geometry checks all exited successfully. The run includes the real ElectroTrace uploaded-ECG command path and the real-service synthetic circulation inverse loop.

The first run passed 93 tests and skipped the unavailable ElectroTrace command adapter. Installing ElectroTrace exposed a validation-environment PATH omission. The notebook now pins ElectroTrace and places its isolated environment's executables on PATH. The final rerun includes a regression test for the corrected ElectroTrace distribution name in runtime provenance.

`HeartTwin_Patient_Lifecycle_Final_Results.zip` retains the protocol, environment, JUnit report and execution logs with 11 SHA-256-checked artifact hashes. Its SHA-256 is `5648f28bb52f6983beed63f42667f4f0cdf3b8535adfd9a522a39f111766ac75`. `colab_evidence.json` records the observed counts, revisions and per-check results. The archive bytes and every manifest-listed member were verified after transfer from Colab.

Browser visual review remains **unverified**: the browser blocked the temporary Colab-hosted workbench URL with `ERR_BLOCKED_BY_CLIENT`. This was not bypassed. Automated workbench HTTP and JavaScript tests passed; those do not replace a visual interaction review.

These results concern software and synthetic infrastructure. They do not establish patient reconstruction accuracy, calibrated clinical uncertainty, patient outcome prediction or treatment benefit.
