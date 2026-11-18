# API

The FastAPI service exposes JSON endpoints and interactive API docs at `/docs`. Run access is scoped to a configured repository and installation and the caller's project membership. Send `Authorization: Bearer <key>` on `/v1` requests. Managed accounts use individual keys. The table lists the equivalent shared roles for the single-project local demo.

| Configured token | Permissions |
|---|---|
| `RELEASETRACE_READ_TOKEN` | Read runs, case evidence, investigations, and saved approvals |
| `RELEASETRACE_MAINTAINER_TOKEN` | Read evidence and request investigations for failed synthetic cases |
| `RELEASETRACE_APPROVER_TOKEN` | Read evidence, record release decisions, and report rollout outcomes |

| Method and path | Contract |
|---|---|
| POST /webhooks/github | Verify the raw-body signature and installation scope, persist by delivery ID, return 202 |
| GET /v1/me | Return the authenticated account handle and its permitted projects and roles. Project IDs are strings. The local demo uses a null handle. |
| GET /v1/projects/{id}/runs | Browse runs with cursor pagination |
| GET /v1/runs/{id} | Return state, evaluation report, GitHub Check status, saved approval, latest recorded rollout outcome, and available reviewer actions |
| GET /v1/runs/{id}/cases | Page through case results, optionally filter for failures |
| POST /v1/runs/{id}/investigations | Accept a failed case ID, return 202 with an investigation ID; deduplicate retries |
| GET /v1/runs/{id}/investigations/{investigation_id} | Read the report and scoped evidence references |
| POST /v1/runs/{id}/approval | Approver submits `decision` (approve/reject) and `image_digest`; returns the saved decision, or 409 for a stale candidate, ineligible run, or conflicting decision |
| GET /v1/runs/{id}/promotion | Return the approved stable and candidate identities if the passing GitHub build evaluation still matches the active build pair. Requires the approver token. |
| POST /v1/runs/{id}/deployments | Record an approver-reported `promoted` or `aborted` outcome with Rollout UID, generation, namespace, name, candidate digest, and stable revision digest. |
| GET /healthz and /readyz | Indicate liveness and whether dependencies are ready |

The run and case lists accept `limit` (default 25, maximum 100) and `after`. Pass the returned `next_cursor` as `after`. Runs sort by delivery ID and cases by case ID.

Approval requires a completed build evaluation that matches the configured candidate commit and digest. An `approve` decision also requires a passing gate. An exact retry returns the saved record. A conflicting or stale request returns HTTP 409. See the [operations guide](OPERATIONS.md#record-a-protected-release-decision).

Deployment reports require a saved approval and matching evaluation digests. A promotion must report the candidate as stable. An abort must report the evaluated baseline as stable. An exact retry for the same run, Rollout UID, generation, and outcome returns the original record. The read endpoint returns the most recently recorded observation. [Recording a rollout outcome](OPERATIONS.md#submit-an-approved-image-to-argo-rollouts)

Handled errors return `code`, `message`, and `request_id`. Keep GitHub App credentials, database configuration, and model keys server-side.
