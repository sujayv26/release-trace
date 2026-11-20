# Restricted pilot runbook

## Rehearse locally from a clean checkout

Use Docker with Compose v2 and a Linux container engine, Python 3.10 or later,
and OpenSSL 1.1.1 or later on `PATH`. Run these commands from a terminal with
access to Docker. The rehearsal uses only synthetic data.

```bash
git clone --branch develop --single-branch https://github.com/sujayv26/release-trace.git release-trace-pilot
cd release-trace-pilot
python -m pip install -r service/requirements.txt
python -m demo.pilot_rehearsal --evidence pilot-evidence.json
```

The command prints the Compose commands, uses a unique project name and
loopback ports, and removes its containers and test volume on exit. It
generates a temporary TLS certificate for the fixture endpoints and injects its
trust bundle into the rehearsal worker. The worker uses the same HTTPS
destination enforcement as the hosted setup. Reviewer keys and the temporary
database dump stay out of the printed evidence. Keep the generated JSON as the
record of the rehearsal.

To include Chromium sign-in and authorization checks, install the test-only
browser dependency and use the same rehearsal as CI. CI uses Chrome from its
runner image, and local runs use Playwright's bundled Chromium:

```bash
python -m pip install playwright==1.61.0
python -m playwright install chromium
python -m demo.pilot_rehearsal --browser --evidence pilot-evidence.json
```

Chromium exercises the production dashboard over a loopback URL. The browser
checks the Secure, HttpOnly, SameSite=Strict cookie and its one-hour lifetime.
The expiry check ages one row in the disposable database. The result records
statuses without collecting keys, cookies, browser traces, or page snapshots.
On Linux, use `python -m playwright install --with-deps chromium` if system
dependencies are absent.

1. Check that `pilot-alpha` passes `refund-window` and `pilot-beta` passes
   `password-reset`, each with its own suite and gate revision and fingerprints.
   The command creates individual keys with `service.accounts`. Expect `404`
   for both cross-project reads and `401` for the disabled beta key.
2. The command switches beta to a different HTTPS candidate path and restarts
   the API. Expect `destination_not_approved`, zero requests to that path,
   and one request to the still-approved baseline.
3. The fixture holds the first evaluation request for `pilot-alpha-recovery`.
   The command kills the worker while waiting, checks that the delivery is
   queued, redelivers it, and restarts the worker. Expect `duplicate` for
   redelivery and exactly one completed run in history. The interrupted request
   itself is repeated after restart. The rehearsal saves the queue monitor's
   stalled and recovered results with the run evidence.
4. The alpha approver records a decision on the recovered passing build and
   submits a synthetic `aborted` rollout observation. Both retries must return
   their original records. The command uses `pg_dump` and `pg_restore` to copy
   the records into `releasetrace_restored`, clears copied browser sessions,
   then starts a separate API against
   it. The returned run, case evidence, approval, and rollout must match the
   source API exactly. The drill also checks that the operator audit history
   in the restored database matches the source. A session valid on the source
   receives HTTP 401 from the restored API.

This drill uses a locally signed GitHub-shaped delivery and a synthetic rollout
observation. Follow the hosted checklist for GitHub App delivery, attestation
verification, and measured hosted canary outcomes.

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

### Configure public ingress limits

Keep the dashboard and API behind an approved edge that supports per-route
limits. Apply controls to public traffic, leaving the dashboard's private
API connection available. Start with these test settings and adjust thresholds
in the edge policy after observing pilot traffic and shared-IP behavior.

| Public route | Starting policy per source IP |
| --- | --- |
| Dashboard `POST /` | 10 per minute. This covers Next.js sign-in and sign-out actions on the home page. |
| API `POST /v1/sessions` | 10 per minute to cover direct session issuance. |
| Other API `/v1/*` | 120 per minute for evidence and operator traffic. Exclude session issuance from this rule. |
| API `POST /webhooks/github` | Separate bucket starting at 120 per minute. Retain signature verification and return 429 on excess traffic. Browser challenges would prevent delivery. |

For a Cloudflare WAF deployment, match the service hostname, method where
shown, and normalized route. Count by the edge-observed client address and
use a block response with HTTP 429. Confirm that the approved edge plan
supports the required rule count and matching fields. On Render, attach
the custom domains, disable the default Render subdomains, and restrict
public origin access to the edge's network using inbound IP rules where
supported. If the selected plan cannot protect the origin, use an approved
gateway with private backends. Verify from outside the edge that direct
origin requests cannot bypass the controls. Keep `/readyz` available to
Render's health checker.
[Cloudflare rate limit configuration](https://developers.cloudflare.com/waf/rate-limiting-rules/)
· [Render inbound IP rules](https://render.com/docs/inbound-ip-rules)
· [Render custom domains](https://render.com/docs/custom-domains)

In a controlled environment, temporarily lower each test threshold, confirm
that legitimate requests below it succeed, then check for HTTP 429 when
exceeding it from the same test source. Confirm normal sign-in and API
access after the window passes. While the reviewer bucket is limited,
deliver and redeliver one signed GitHub event. Expect HTTP 202 for both and
one evaluation and Check record. Restore the chosen thresholds and record
the rule IDs, limits, response statuses, and timestamps. Keep credential
headers, cookies, and request bodies out of edge logging and test artifacts.

## Collect hosted verification evidence

### Evaluate Runbook RAG

From the repository root, install `service/requirements.txt` and run
`python -m runbook_rag.evaluate` for the eight-case starter regression suite.
Run `python -m runbook_rag.benchmark` for the fixed 13-case synthetic holdout
and per-case results. The benchmark uses the same code paths as the API,
with 30 timed calls per case after an initial warm-up call.

| Variant | Recall@3 | MRR@3 | Correct citations | Correct abstentions | p95 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| SQLite FTS5 BM25 | 1.00 | 1.00 | 6/9 | 4/4 | 1.860 |
| BM25 + synonym-normalized character trigram similarity | 1.00 | 1.00 | 9/9 | 4/4 | 1.735 |
| Hybrid + title and excerpt overlap reranking | 1.00 | 1.00 | 9/9 | 4/4 | 2.009 |

These local Windows Python 3.12 timings include scoring and excerpt selection
in process. All variants retrieved zero superseded documents on the active
corpus. Hybrid recovered three paraphrases with no measured ranking or citation
benefit from the extra reranking step, so hybrid is the deployment default.
The small synthetic holdout and local timing exclude network overhead. The
service returns verbatim excerpts and makes no model calls.

On a Linux Docker host with a full Git checkout, install the service requirements
and run `bash runbook_rag/ci-rehearse.sh /tmp/runbook-evidence`. The script uses a
disposable runner with ports 5000 and 18080–18082 available. For a manual run,
remove only the four containers created by the script when finished. CI archives
the gate reports, target identities, structured traces and benchmark. The
rehearsal verifies that the stable build cites v2, the stale candidate cites
v1 and is blocked, and the corrected candidate passes its own evaluation.
All three must abstain on the missing-evidence question. Preserve the three
revision commits in history so the rehearsal can rebuild them.

For the hosted rehearsal, build from the same three commits for Linux amd64
and push to the approved registry. Record each commit and registry manifest
digest from the build job. Deploy each image using its immutable digest. Set
`RUNBOOK_BUILD_COMMIT` and `RUNBOOK_IMAGE_DIGEST` from the verified build record
and running deployment reference. Configure an independent random
`RUNBOOK_ACCESS_TOKEN` for each service through Render secrets. The service
requires valid metadata and a bearer credential at startup in release images.
It uses the same retrieval path for `/ask` and `/evaluate`. Set readiness
to `/health` and use dedicated candidate services without user traffic.
[Render image deployments](https://render.com/docs/deploying-an-image)

Point the managed project's evaluation file at
`runbook_rag/data/release-suite.json`, copied as an API secret file. Pin the
stable and current candidate identities in the project's targets file and
configure their exact HTTPS endpoints and project-scoped tokens on the worker.
Confirm the endpoint identities match the registry and deploy records. Rotate
the candidate settings from stale to corrected before delivering the matching
push. Keep the evaluator pinned to the stable build throughout.

Capture the `runbook_request` events from the service log for the test window.
They contain commit and image identity, effective index revision, corpus hash,
cited document version, retrieval and validation durations, optional reranking
duration, and an abstention reason. Keep questions, excerpts, and bearer tokens
out of the verification record.

### Use your own runbook corpus

Provide a UTF-8 JSON array of section-sized passages, with stable versioned IDs.

\`\`\`json
[
  {
    "id": "checkout/rollback-v2",
    "title": "Checkout deployment rollback",
    "status": "active",
    "excerpt": "Pause traffic promotion and obtain release-lead approval before rollback."
  }
]
\`\`\`

Set \`RUNBOOK_CORPUS_FILE\` to the path visible to the service. For a release
image, also set \`RUNBOOK_CORPUS_SHA256\` to the SHA-256 of the exact file bytes.
Keep the corpus read-only in the deployment and restart with a new fingerprint
when it changes. Stable and candidate revisions can pin different corpora.
The service accepts one to 512 passages, up to 3,000 characters per excerpt and
2 MB total. It excludes superseded passages from both retrieval indexes.
Keep separate corpora in separate deployments with independent bearer tokens.

The default hybrid mode runs offline. To opt in to dense retrieval and
model-assisted quote selection, supply \`OPENAI_API_KEY\` through the deployment's
secret mechanism and set \`RUNBOOK_OPENAI_EMBEDDING_MODEL\` and
\`RUNBOOK_OPENAI_ANSWER_MODEL\` to available embedding and structured-output
models. Document passages are embedded at startup. Requests send the question
and shortlisted passages to OpenAI in this mode, so enable it only for data
approved for that provider. The optional \`RUNBOOK_OPENAI_MIN_SIMILARITY\`
defaults to 0.45 and should be calibrated against the project's evaluation suite.
The bounded corpus is scanned in memory for dense search. Size the service based
on measured startup time and concurrency before using a larger workload.

The API joins at most two verbatim quotes from retrieved passages. It validates
the passage IDs and quotes before returning them and uses the first citation
in the existing ReleaseTrace evaluation response. An explicit model abstention
returns insufficient evidence. If a request to the provider fails or returns
invalid evidence, the service falls back to local retrieval and records which
stage failed. Startup fails if the configured embedding index cannot be built.
Verbatim validation proves source attribution. Use a held-out evaluation to
check whether the selected evidence actually answers each question.

Run locally from the repository root after installing
\`service/requirements.txt\`:

\`\`\`bash
python -m uvicorn runbook_rag.api:app
\`\`\`

Open the API documentation at http://127.0.0.1:8000/docs. Send
\`{"question":"What is the checkout deployment rollback procedure?"}\` to
\`POST /ask\` to try the bundled corpus. Release images require their pinned
commit, image digest, and bearer credentials.

### Two-minute reviewer walkthrough

Open the dashboard URL supplied with your invitation and sign in using your
individual key. Open your assigned project and select the blocked run. Select
**Failed cases** and compare the stable and candidate citations for the critical
case. Open the corrected run and check that it has its own passing verdict,
evaluated commit, and candidate digest. If you have approval access, record an
approval against that digest. Sign out when finished.

| Order | Exercise | Evidence to save |
|---|---|---|
| 1 | Confirm HTTPS for the dashboard and API, API readiness, and a private PostgreSQL connection from the API and worker. | Dashboard URL, deployed commit, readiness result, migration revision, and timestamp. |
| 2 | Onboard two test projects with different suites and approved evaluation URLs. Give each reviewer access to one project. Attempt a cross-project read, revoke a test key, and evaluate a candidate URL missing from the approved map. | Two completed run IDs, a cross-project HTTP 404, a revoked-key HTTP 401, and an inconclusive run with `destination_not_approved`. Confirm the denied endpoint received zero requests. |
| 3 | Install the GitHub App on a demo repository you control. Enable the worker publisher for that managed project and use the live redelivery checklist. | The signed delivery ID, evaluated commit, Check ID, and proof that redelivery preserved both IDs. [Live GitHub verification](OPERATIONS.md#verify-live-github-app-redelivery) |
| 4 | Publish the candidate image and its build-provenance attestation from the trusted GitHub Actions workflow. Try promotion after the active build pair changes, then exercise healthy and unhealthy canaries for approved builds. | Stale-approval HTTP 409, the verified workflow and source commit, the approved and running digest, and a saved dashboard outcome for each rollout. |
| 5 | Have a second operator onboard a test project, recover an interrupted evaluation, and restore evidence into an isolated PostgreSQL instance. | Onboarding record, detection and recovery times, one logical run and decision after recovery, and restored run, approval, and rollout IDs. |

### Verify image provenance before applying a rollout

The kind canary rehearsal is covered by its own CI workflow. A hosted
Kubernetes rollout requires a separately approved staging environment.

Run from a staging operator environment with GitHub CLI and registry access.
Set `attestation_workflow` in the managed project file to the exact workflow
trusted to sign builds for the evaluated repository. The promotion command
uses that setting and rejects an override to a different signer. The command
verifies the candidate's OCI digest and evaluated commit before the Kubernetes
patch. It then waits to record the outcome.

```bash
python -m staging.promote_approved DELIVERY_ID \
  --api-url https://releasetrace.example.com --context staging \
  --namespace support --rollout support --container support \
  --attested-by OWNER/REPO/.github/workflows/build.yml --apply --observe 120
```

Use `--attestation-bundle PATH` to verify a downloaded signed bundle. The
canary workflow uses this path with its disposable
registry. It signs the candidate digest in GitHub Actions, checks that a
different commit and image are rejected, and saves the bundle and verified
commit-to-digest mapping with the observed rollout.

Use the healthy rehearsal to verify the stable workload runs the candidate
digest. Use a staged health regression for the abort exercise and verify the
stable service keeps answering from the prior revision. Collect both
dashboard outcomes before completing the drill. [GitHub CLI attestation verification](https://cli.github.com/manual/gh_attestation_verify)

### Rehearse recovery and restore

Run `python -m service.queue_health --max-age-seconds 300` from a scheduled
monitor with private database access and the same `RELEASETRACE_DATABASE_URL`.
Run it independently of the evaluation worker. Route exit 1 (stalled
evaluations) and exit 2 (database unavailable) to the operator alert channel.
The command reports an aggregate count and avoids exposing run payloads or
credentials. During the interruption drill, record the alert delivery and
verify the next probe exits 0 after the worker clears the queue. Choose a
threshold above the normal end-to-end evaluation time for your largest suite.

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

Keep ingress closed. In the operator environment, verify
`RELEASETRACE_DATABASE_URL` points to the **isolated restore target**, apply
the migration, and clear browser sessions before starting its API:

```bash
python -m service.store
python -m service.authentication clear-sessions --confirm
```

The command prints only the number of removed sessions. Start the isolated
API with the restored database and matching project configuration. Verify
that a previously valid session receives HTTP 401 and that allowed reviewers
can sign in again. If restoring a snapshot predates a key rotation or account
change, reconcile key rotations, account disables, and project grants from
the trusted operator record before reopening ingress.
Verify the same run result, approval actor and digest, rollout
outcome, and stable digest. Compare the account audit history in both databases
using `python -m service.accounts history`. Keep the dump and audit output in
approved storage.

### Record the second operator's rehearsal

Have the second operator use this guide to onboard a project and complete the
interruption and isolated restore. Record the operator, date, environment,
run and approval IDs, alert and recovery times, audit comparison result, and
steps that needed clarification. Keep reviewer keys and document contents out
of the rehearsal record.
