# Dashboard design

Use a compact engineering dashboard with neutral surfaces, readable typography, and consistent status labels. Reserve red for blockers and green for verified success. Pair color with text and icons. At small widths, stack comparisons and allow horizontal scrolling only for data grids.

| Screen | Information and actions |
|---|---|
| Release history | Commit, evaluation status, gate, candidate and timestamp |
| Run detail | Stable and candidate summaries, critical cases, build provenance, GitHub Check status, and saved release decision |
| Case results | Expected citation, both variants, policy evidence, and the maintainer's Investigate action for failed synthetic cases |
| Investigation | Supported or inconclusive outcome, reason, changed setting, and observed citation |
| Approval | Candidate digest, gate evidence, actions for the approver, and saved reviewer and timestamp |

Rollout measurements currently appear in the [canary drill artifact](../README.md#verification-and-rollout-evidence). Displaying deployment state on the run page is future work.

Use keyboard-accessible controls and visible focus. Distinguish pending, empty, failed and inconclusive states. Keep prior evidence visible during a retry and show absent metrics explicitly.
