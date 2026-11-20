# Database

ReleaseTrace uses SQLAlchemy and Alembic. PostgreSQL backs Compose and the integration job in CI. SQLite supports local service checks.

| Table | Records and constraints |
|---|---|
| `webhook_deliveries` | Provider delivery ID as primary key, repository and installation scope, commit SHA, queue state, pinned JSON inputs, JSON evaluation report, timestamps, and GitHub Check publication state. Case results live inside the report. |
| `investigations` | Deterministic ID, delivery foreign key, case ID, queue state, and JSON report with the controlled experiment when one ran. Requests for the same run and case reuse the ID. |
| `approvals` | One decision per delivery. Stores the exact image digest, decision, actor, and creation timestamp. |
| `deployments` | Append-only operator observations keyed by approved delivery, Rollout UID, generation, and outcome. Stores the target and stable image digests and when the API recorded the outcome. |
| `accounts` | Reviewer handle and a unique access-key hash. A disabled account has no active key hash. |
| `browser_sessions` | Hashed opaque session token, issuing credential fingerprint, and expiry. The expiry index supports pruning during sign-in. |
| `project_owners` | Persistent binding from a managed GitHub repository ID to an organization slug. |
| `project_memberships` | A reviewer role scoped to the account handle, repository, installation, and organization. |
| `access_requests` | One current viewer request per account and repository, including its status, unique decision ID and project binding at submission. Approval and its operator audit event commit together. |
| `account_access_events` | Successful operator account commands with time, account, operator label when supplied, and project scope for grant and revoke. Written in the same transaction as the command. |

The worker saves the evaluation claim and report in one transaction. An interruption before commit leaves the delivery queued. A Temporal retry after commit reuses the completed report. Investigation reads and approval writes are scoped through the associated delivery.

Project-specific inputs store the full, normalized suite and gate policy with their revision labels and fingerprints. Both the worker and case evidence API read the saved snapshot. Completed reports include the revisions and fingerprints shown in the dashboard.

Run `python -m service.store` with the configured `RELEASETRACE_DATABASE_URL` before starting an updated API or worker. The migration head is `0010_access_requests`. The migration test covers adoption of a legacy schema and preservation of saved reports. Older runs can have missing timestamps.

`python -m service.accounts history` lists commands recorded after the audit migration. Access keys and their hashes are excluded from this history. Approval and rollout audit evidence remains in the `approvals` and `deployments` tables. Evidence retention policies are future work.
