# Implementation plan

| Phase | Delivered | Open work |
|---|---|---|
| 1 · Reproduce a regression | The stable fixture cites the current account-closure policy. A controlled retrieval swap restores that citation on the candidate. | |
| 2 · Gate the release | A critical failure blocks a candidate scoring 80%. Missing required evidence prevents promotion. | |
| 3 · Connect review | Signed intake, Postgres-backed evaluation, dashboard, and GitHub Check preview and publishing paths. | Verify live GitHub App delivery, redelivery, and the Check on the evaluated commit. |
| 4 · Investigate | Bounded LangGraph reruns and a six-case offline holdout. The hosted benchmark adapter is available. | Collect and analyze live model results. |
| 5 · Recover and approve | Linux CI verified Temporal worker replacement. Compose verified digest binding, authorization, and retry. Pipeline tests cover stale candidates. | |
| 6 · Stage a rollout | A kind drill observed healthy promotion and automatic canary abort with measured recovery. | Deploy a real evaluated OCI image after checking its saved approval. |

Start with the [local demo](../README.md#run-the-local-demo). The [operations guide](OPERATIONS.md) covers live verification and optional workers. The [product requirements](PRD.md) describe the intended release workflow.
