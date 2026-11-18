# Database

Use PostgreSQL with Alembic migrations. Use UUID primary keys, timestamptz timestamps and validated JSONB for version manifests and evidence envelopes.

| Record | Key fields and constraints |
|---|---|
| projects/memberships | Repository ID, installation ID; membership unique by project and actor, with viewer/maintainer/approver role |
| releases | Project FK, commit SHA, image digest, version manifest; unique by project, commit and digest |
| webhook_deliveries | Provider delivery ID unique, event state and timestamp; persist dispatch intent in the same transaction |
| runs | Project and release FKs, dataset/evaluator/policy revisions, state and gate; index project and creation time |
| case_results | Run FK, case ID, variant, repeat, score and evidence reference; unique by run, case, variant and repeat |
| investigations/experiments | Run and case reference, outcome and evidence; canonical intervention key unique per investigation |
| approvals/deployments | Project/run FK, exact digest, actor or rollout ID, decision and timestamps |
| audit_events | Project ID, actor, action, target and timestamp |

Scope lookups by project and verify references belong to it. Retain redacted evidence and expire it per policy. Seed synthetic data only. Test migrations against fresh and existing schemas and verify restoration before hosted rollout.