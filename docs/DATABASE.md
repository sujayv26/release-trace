# Database

ReleaseTrace uses SQLAlchemy and Alembic. PostgreSQL backs Compose and the integration job in CI. SQLite supports local service checks.

| Table | Records and constraints |
|---|---|
| `webhook_deliveries` | Provider delivery ID as primary key, repository and installation scope, commit SHA, queue state, pinned JSON inputs, JSON evaluation report, timestamps, and GitHub Check publication state. Case results live inside the report. |
| `investigations` | Deterministic ID, delivery foreign key, case ID, queue state, and JSON report with the controlled experiment when one ran. Requests for the same run and case reuse the ID. |
| `approvals` | One decision per delivery. Stores the exact image digest, decision, actor, and creation timestamp. |
| `deployments` | Append-only operator observations keyed by approved delivery, Rollout UID, generation, and outcome. Stores the target and stable image digests and when the API recorded the outcome. |

The worker saves the evaluation claim and report in one transaction. An interruption before commit leaves the delivery queued. A Temporal retry after commit reuses the completed report. Investigation reads and approval writes are scoped through the associated delivery.

Run `python -m service.store` with the configured `RELEASETRACE_DATABASE_URL` before starting an updated API or worker. The migration head is `0006_deployments`. The migration test covers adoption of a legacy schema and preservation of saved reports. Older runs can have missing timestamps.

Organization memberships, audit events, and evidence retention policies are future schema work.
