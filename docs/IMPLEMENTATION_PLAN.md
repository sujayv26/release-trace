# Implementation plan

| Phase | Deliverable | Acceptance check |
|---|---|---|
| 1 | Local support fixture, provenance manifests and retrieval swap | Stable passes, candidate cites old policy, swap restores expected citation, retry produces one logical experiment |
| 2 | Deterministic evaluator and release policy | A critical case blocks a high-scoring candidate and missing evidence prevents promotion |
| 3 | API, persistent worker, dashboard and GitHub check | Event on an evaluated commit is deduplicated and unauthorized reads fail |
| 4 | LangGraph investigator and held-out benchmark | Compare against rules and a one-shot model; explain diagnosis errors and abstentions |
| 5 | Temporal recovery and protected promotion | Restart a worker without duplicate results, and reject an approval for a superseded candidate |
| 6 | Staging rollout and portfolio evidence | Record healthy promotion, unhealthy canary abort and measured recovery |

Start with the offline regression and controlled rerun in the [agent guide](ReleaseTrace-Agent-Build-Guide.md). Add each dependency when its phase needs it.