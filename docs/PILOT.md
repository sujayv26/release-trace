# Restricted pilot runbook

## Prepare the hosted environment

The [Render Blueprint](../render.yaml) defines a dashboard and an API with HTTPS ingress, a background worker, and PostgreSQL on the private network. It uses the `develop` branch and disables automatic service deploys. PostgreSQL has no public IP allowlist entries. The dashboard talks to the API over the private network. Reviewers authenticate with managed account keys. GitHub pushes require the webhook signature and a configured repository and installation.

Provision only in an approved pilot workspace. In Render, select the `develop` branch when creating the Blueprint. The API runs the database migration before deployment. Add `projects.json` and any referenced build target and evaluation files as API secret files under `/etc/secrets`. The API requires that configuration at startup. Verify the API's `/readyz` check before testing the dashboard or worker. Use synthetic data until the deployment and access checks have passed. [Managed project configuration](OPERATIONS.md#configure-managed-projects-and-reviewer-accounts)

Set `RELEASETRACE_APPROVED_EVALUATION_URLS` on the worker. Keys are repository IDs and each value maps a variant to the exact approved HTTPS URL.

```json
{
  "12345": {
    "baseline": "https://stable.example.com/evaluate",
    "candidate": "https://candidate.example.com/evaluate"
  }
}
```

The worker checks this configuration before making requests or attaching a bearer credential. In the pilot, `RELEASETRACE_REQUIRE_APPROVED_DESTINATIONS=1` makes omitted or invalid approval configuration deny evaluation requests. Removing a URL also stops it from being used by already queued runs. Record the denial as `destination_not_approved` in the case report. Configure any repository-specific evaluation credentials on the worker, and grant reviewer keys privately from the operator CLI. [Application HTTP contract](OPERATIONS.md#application-evaluation-http-contract)

## Collect hosted verification evidence

| Order | Exercise | Evidence to save |
|---|---|---|
| 2 | Onboard two test projects with different suites and approved evaluation URLs. Give each reviewer access to one project. Attempt a cross-project read, revoke a test key, and evaluate a candidate URL missing from the approved map. | Two completed run IDs, a cross-project HTTP 404, a revoked-key HTTP 401, and an inconclusive run with `destination_not_approved`. Confirm the denied endpoint received zero requests. |
| 3 | Install the GitHub App on a demo repository you control. Use the live redelivery checklist and a dedicated publisher configured for that installation. | The signed delivery ID, evaluated commit, Check ID, and proof that redelivery preserved both IDs. [Live GitHub verification](OPERATIONS.md#verify-live-github-app-redelivery) |
| 4 | Publish the candidate image and its build-provenance attestation from the trusted GitHub Actions workflow. Try promotion after the active build pair changes, then exercise healthy and unhealthy canaries for approved builds. | Stale-approval HTTP 409, the verified workflow and source commit, the approved and running digest, and a saved dashboard outcome for each rollout. |
| 5 | Have a second operator onboard a test project, recover an interrupted evaluation, and restore evidence into an isolated PostgreSQL instance. | Onboarding record, detection and recovery times, one logical run and decision after recovery, and restored run, approval, and rollout IDs. |

### Verify image provenance before applying a rollout

Run from a staging operator environment with GitHub CLI and registry access.
Supply the exact workflow that is trusted to sign builds for the evaluated
repository. The command verifies the candidate's OCI digest and evaluated
commit before the Kubernetes patch. It then waits to record the outcome.

```bash
python -m staging.promote_approved DELIVERY_ID \
  --api-url https://releasetrace.example.com --context staging \
  --namespace support --rollout support --container support \
  --attested-by OWNER/REPO/.github/workflows/build.yml --apply --observe 120
```

Use the healthy rehearsal to verify the stable workload runs the candidate
digest. Use a staged health regression for the abort exercise and verify the
stable service keeps answering from the prior revision. Collect both
dashboard outcomes before completing the drill. [GitHub CLI attestation verification](https://cli.github.com/manual/gh_attestation_verify)

### Rehearse recovery and restore

1. Queue a test release and record its delivery ID and queue time. Stop the
   evaluation worker during processing in the isolated environment. Use the
   run detail and worker status or logs to identify the stalled test run.
2. Restart the worker or run `python -m service.worker --once` against the
   same test database. Read the same delivery again. Verify that exactly one
   completed run and the original approval exist after retry. For Temporal,
   use the [worker replacement drill](OPERATIONS.md#run-evaluations-with-temporal).
3. Take a custom-format PostgreSQL backup after recording a test rollout.
   Restore into an empty database in the isolated environment. Use operator
   PostgreSQL service definitions and credentials stored outside the repository.

```bash
pg_dump --dbname='service=release_pilot' --format=custom --file=releasetrace.dump
pg_restore --dbname='service=release_restore' --exit-on-error --single-transaction releasetrace.dump
```

Start an isolated API with the restored database and the matching project
configuration. Verify the same run result, approval actor and digest, rollout
outcome, and stable digest. Keep the dump in approved storage.
