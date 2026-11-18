# Dashboard design

Use a compact engineering dashboard with neutral surfaces, readable typography, and consistent status labels. Reserve red for blockers and green for verified success. Pair color with text and icons. At small widths, stack comparisons and allow horizontal scrolling only for data grids.

| Screen | Information and actions |
|---|---|
| Release history | Commit, evaluation status, gate, candidate and timestamp |
| Run detail | Baseline and candidate summaries, critical cases, provenance and rollout status |
| Case detail | Expected policy, both outputs, evidence links and Investigate action |
| Investigation | Hypothesis, changed setting, observed result, uncertainty and suggested next check |
| Approval | Candidate digest and gate evidence, reviewer decision and stale-candidate feedback |

Use keyboard-accessible controls and visible focus. Distinguish pending, empty, failed and inconclusive states. Keep prior evidence visible during a retry and show absent metrics explicitly.