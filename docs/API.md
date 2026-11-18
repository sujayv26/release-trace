# API

Use JSON and typed request/response schemas. Return a stable error code, safe message and request ID. Validate authorization server-side and scope all evidence to the caller's project.

| Method and path | Contract |
|---|---|
| POST /webhooks/github | Verify the raw-body signature and installation scope, persist by delivery ID, return 202 |
| GET /v1/projects/{id}/runs | Browse runs with cursor pagination |
| GET /v1/runs/{id} | Return state, provenance, gate and summaries |
| GET /v1/runs/{id}/cases | Page through case results, optionally filter for failures |
| POST /v1/runs/{id}/investigations | Accept a failed case ID, return 202 with an investigation ID; deduplicate retries |
| GET /v1/runs/{id}/investigations/{investigation_id} | Read the report and scoped evidence references |
| POST /v1/runs/{id}/approval | Record a decision for the evaluated digest; return 409 for stale candidates |
| GET /healthz and /readyz | Indicate liveness and whether dependencies are ready |

Keep GitHub App credentials, database configuration and model keys server-side. Apply request bounds and timeouts. Missing required evidence yields an inconclusive gate that blocks promotion.