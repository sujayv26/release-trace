# API

The FastAPI service exposes JSON endpoints and interactive API docs at `/docs`. Run access is scoped to the configured repository and installation. Send `Authorization: Bearer <token>` on `/v1` requests.

| Configured token | Permissions |
|---|---|
| `RELEASETRACE_READ_TOKEN` | Read runs, case evidence, investigations, and saved approvals |
| `RELEASETRACE_MAINTAINER_TOKEN` | Read evidence and request investigations for failed synthetic cases |
| `RELEASETRACE_APPROVER_TOKEN` | Read evidence and record release decisions |

| Method and path | Contract |
|---|---|
| POST /webhooks/github | Verify the raw-body signature and installation scope, persist by delivery ID, return 202 |
| GET /v1/projects/{id}/runs | Browse runs with cursor pagination |
| GET /v1/runs/{id} | Return state, evaluation report, GitHub Check status, saved approval, and available reviewer actions |
| GET /v1/runs/{id}/cases | Page through case results, optionally filter for failures |
| POST /v1/runs/{id}/investigations | Accept a failed case ID, return 202 with an investigation ID; deduplicate retries |
| GET /v1/runs/{id}/investigations/{investigation_id} | Read the report and scoped evidence references |
| POST /v1/runs/{id}/approval | Approver submits `decision` (approve/reject) and `image_digest`; returns the saved decision, or 409 for a stale candidate, ineligible run, or conflicting decision |
| GET /healthz and /readyz | Indicate liveness and whether dependencies are ready |

The run and case lists accept `limit` (default 25, maximum 100) and `after`. Pass the returned `next_cursor` as `after`. Runs sort by delivery ID and cases by case ID.

Approval requires a completed build evaluation that matches the configured candidate commit and digest. An `approve` decision also requires a passing gate. An exact retry returns the saved record. A conflicting or stale request returns HTTP 409. See the [operations guide](OPERATIONS.md#record-a-protected-release-decision).

Handled errors return `code`, `message`, and `request_id`. Keep GitHub App credentials, database configuration, and model keys server-side.
