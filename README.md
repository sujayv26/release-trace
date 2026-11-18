# ReleaseTrace

ReleaseTrace is an AI release verification project in development. It compares stable and candidate versions and investigates failed cases through controlled reruns.

## Run the full demo in Docker Compose

From the repository root, run these commands with Docker and Compose v2:

```bash
docker compose up --build --wait --wait-timeout 90
docker compose exec -T api python -m service.seed_demo
```

Compose starts PostgreSQL, runs the one-shot `migrate` service, then starts
the API, worker, and dashboard. The seed command sends a signed synthetic push.
Repeating it returns the same delivery ID. To apply pending migrations
explicitly, run `docker compose run --rm --build migrate`.

Open [the dashboard](http://localhost:3000) with `demo-maintainer-token`, open
the blocked run, and select **Investigate** on the failed account-closure case.
Refresh the report once the worker has finished. `demo-viewer-token` gives
read-only access. These defaults are for the local demo. The API and dashboard
bind to loopback, and PostgreSQL is reachable on the Compose network. To
change the host ports, set `RELEASETRACE_API_PORT` or `RELEASETRACE_DASHBOARD_PORT`.

Run `docker compose exec -T api python -m service.smoke_demo` to check the
signed event, duplicate delivery, blocked release, and successful controlled
rerun. GitHub Actions runs the same smoke against a fresh stack. Use
`docker compose down` to stop the stack. The named volume retains saved runs.

The Compose build uses digest-pinned base images, the dashboard's npm lockfile,
and `service/requirements.compose.lock`. To refresh the Python lock after a
dependency change, run `uv pip compile service/requirements.txt --universal
--python-version 3.12 --no-header --output-file service/requirements.compose.lock`.

## Run the first milestone locally

Requires Python 3.10 or later. The demo runs without external dependencies or credentials.

```bash
python demo/release_trace_demo.py
python -m unittest discover -s demo -v
```

The stable configuration cites the current account-closure policy. The candidate cites a superseded policy. Restoring only the baseline retrieval setting makes the candidate cite the current policy. The report includes configuration fingerprints and the selected source evidence.

See the [implementation plan](docs/IMPLEMENTATION_PLAN.md), [architecture](docs/ARCHITECTURE.md), [investigator guide](docs/ReleaseTrace-Agent-Build-Guide.md), and [Codex instructions](AGENTS.md).

## Run the release gate

```bash
python demo/release_gate.py
```

The synthetic candidate passes four of five cases and meets the 80% aggregate
threshold. The failed account-closure case is critical, so the gate blocks the
release. Missing required evidence prevents promotion.

Develop on `develop` and review changes through a pull request to `main`.
GitHub Actions runs Python compilation, unit tests and both local demos.

## Run the webhook and worker

This Phase 3 service evaluates the checked-in synthetic fixture suite. It stores
the push commit SHA as event provenance and labels the result `synthetic_fixture`.

Install `service/requirements.txt`. Set the same `RELEASETRACE_DATABASE_URL` in
both terminals, using an absolute `sqlite:///` database URL for a local demo or
a `postgresql+psycopg://` URL for PostgreSQL. For the API, set
`RELEASETRACE_WEBHOOK_SECRET`, `RELEASETRACE_READ_TOKEN`,
`RELEASETRACE_REPOSITORY_ID`, and `RELEASETRACE_INSTALLATION_ID`.

```bash
python -m service.store
python -m uvicorn service.api:create_app --factory
```

Run `python -m service.store` before starting an updated API or worker. It
applies pending Alembic migrations and preserves existing demo runs. Runs
saved before timestamp tracking show their original times as unavailable.

In the second terminal, run `python -m service.worker`. A signed GitHub App
branch push to `/webhooks/github` queues one job per delivery ID. Read its state
and evidence at `/v1/runs/{delivery_id}` with the configured bearer token. The
API exposes `/healthz` and `/readyz`.

Use the same bearer token for these reviewer endpoints:

- `GET /v1/projects/{repository_id}/runs` lists commits, run states, and gate verdicts.
- `GET /v1/runs/{delivery_id}/cases?outcome=fail` lists failed candidate cases
  with the expected citation and evidence from both versions. Omit `outcome`
  to see every case.

Both lists accept `limit` (1 to 100, default 25) and `after`. Pass the returned
`next_cursor` as `after` to read the next page. Runs are ordered by delivery ID
and cases by case ID. A queued run returns an empty case list with its state.

For key-free SQLite migration and integration checks, run
`python -m unittest service.test_schema service.test_pipeline service.test_github_check -v`. The
PostgreSQL CI job applies the same migrations and runs the pipeline test
against a fresh PostgreSQL 16 database.

## Review a release in the dashboard

Start the API and worker using the local settings above. To create a sample run
without GitHub credentials, run `python -m service.seed_demo` against the local
API on port 8000. Set the same repository ID, installation ID, and webhook
secret in the terminal running the seed command. The worker picks up the sample
delivery and saves the gate decision. Repeating the command reuses that delivery.

The dashboard requires Node.js 20.9 or newer. Give the dashboard process the
same `RELEASETRACE_REPOSITORY_ID` as the API. The dashboard uses
`http://127.0.0.1:8000` by default. Set `RELEASETRACE_API_URL` if the API runs at
a different address.

```bash
cd dashboard
npm ci
npm run dev
```

Open [the local dashboard](http://localhost:3000) and enter the API's
`RELEASETRACE_READ_TOKEN`. Select the sample commit and then **Failed cases** to
see why the candidate was blocked. The dashboard currently uses one project
viewer token. Serve the production build over HTTPS for its secure session cookie.

## Preview a GitHub Check

With the same database URL, repository ID, and installation ID used by the API,
run `python -m service.github_check <delivery_id>` after the worker completes
the evaluation. Fixture checks are neutral. Build checks report success after
a passing gate and failure after a blocked or inconclusive gate.

Publishing is a separate, explicit step for a completed delivery from GitHub.
Configure `RELEASETRACE_GITHUB_REPOSITORY` (`owner/repo`),
`RELEASETRACE_GITHUB_APP_ID`, and `RELEASETRACE_GITHUB_INSTALLATION_TOKEN` from
the configured installation with repository **Checks: write** access. Run
`python -m service.github_check <delivery_id> --publish`. Run publish commands
for the same delivery serially. A retry first looks for an existing check from
the app with the same reference. The local seed uses a placeholder SHA and is
available for preview.

## Evaluate two running builds

The endpoint evaluator calls both builds with the same checked-in test cases
and prints a JSON report with dataset, policy, and build fingerprints. For a
key-free demonstration, start each target in a separate terminal after
installing `service/requirements.txt`:

```bash
python -m demo.build_target baseline
python -m demo.build_target candidate
```

In a third terminal, run:

```bash
python -m service.evaluate_builds demo/build_targets.json
```

The candidate still passes 80% of the cases. Its wrong account-closure citation
blocks the release. Exit codes are `0` for pass, `1` for block, and `2` for an
inconclusive run or invalid command configuration.

For your own builds, use the same [target file shape](demo/build_targets.json)
and point to each build's evaluation endpoint. Each endpoint accepts a JSON
`POST` body with `case_id` and `question` and returns
`{"build":{"commit_sha":"…","image_digest":"sha256:…"},"citation":"…"}`.
Return `null` when there is no citation. Populate build identity from trusted
deployment metadata. ReleaseTrace compares the endpoint's reported identity
with the configured values. Remote endpoints use HTTPS, and optional bearer
tokens come from `RELEASETRACE_BASELINE_EVALUATION_TOKEN` and
`RELEASETRACE_CANDIDATE_EVALUATION_TOKEN`. This path runs from the CLI and writes
the report to standard output.

### Run build evaluations from GitHub pushes

Set `RELEASETRACE_BUILD_TARGETS_FILE` on the API to the target file and restart
the API when the build pair changes. The candidate must be deployed and its
commit SHA must match the signed push. The API snapshots the build pair with
the job. In this mode, pushes for other commits are ignored. Optional tokens
belong in the worker's environment. Scope them to their respective endpoints.

To run the full flow locally, start both demo targets, set
`RELEASETRACE_BUILD_TARGETS_FILE=demo/build_targets.json` on the API, and start
the API and worker. Run `python -m service.seed_demo --targets demo/build_targets.json`
and open the resulting run in the dashboard. Use its delivery ID to preview
the GitHub Check.

### Publish build checks automatically

Run `python -m service.store` to apply the Check tracking migration. Install a
GitHub App with repository **Checks: write** on the selected repository. In the
worker environment, set `RELEASETRACE_GITHUB_APP_ID`,
`RELEASETRACE_GITHUB_REPOSITORY` (`owner/repo`),
`RELEASETRACE_GITHUB_APP_KEY_FILE` (path to the App's private PEM on the server),
and the same `RELEASETRACE_REPOSITORY_ID` and `RELEASETRACE_INSTALLATION_ID`
used by the API. Keep the private key outside the repository.

Start `python -m service.worker --publish-checks`. It publishes completed build
checks for the configured repository, records the Check ID, and retries
temporary failures. Earlier completed build deliveries are also eligible.
Seeded demo deliveries and synthetic fixtures are preview-only in this worker.
Use the automated publisher or the manual publishing command for a delivery.

### Verify live GitHub App redelivery

Use a demo repository you control and an HTTPS endpoint that routes GitHub to
`/webhooks/github`. Configure the App for push events with repository
**Contents: read** and **Checks: write**, install it on that repository, and
configure the API webhook secret and worker key on the host. Deploy the two
evaluation targets with the real build identities. Set the candidate SHA in
the target file before delivering the push. The local Compose demo uses test
IDs and credentials. [GitHub push event permissions](https://docs.github.com/en/webhooks/webhook-events-and-payloads#push)

1. Record the push delivery GUID and candidate SHA. Read
   `GET /v1/runs/{delivery_id}` and wait for `github_check_id`. The saved run's
   `commit_sha` and GitHub Check's `head_sha` should both match the candidate SHA.
   The Check's `external_id` should equal the delivery GUID.
2. Redeliver that same push from the GitHub App's **Recent deliveries**. The API
   should return HTTP 202 with `status: duplicate`. Confirm the saved run keeps
   its original Check ID. List the Check runs for that exact SHA and confirm
   there is exactly one from the App with that name and delivery GUID.
   [GitHub App redelivery](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks#redelivering-github-app-webhooks) · [Check Runs API](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference)

## Investigate a failed synthetic case

Apply migrations with `python -m service.store` and configure a separate
`RELEASETRACE_MAINTAINER_TOKEN` on the API. Sign in to the dashboard with that
token, open a completed synthetic run, and select **Investigate** on a failed
case. Refresh the report when the worker finishes. The case then has a **View
investigation** link for both maintainers and viewers.

To start the same flow from the API,
send `POST /v1/runs/{delivery_id}/investigations` with the maintainer bearer
token and `{"case_id":"account-closure"}`. The same run and case return the
same investigation ID on retry.

The worker executes the rerun. Read it with the viewer or maintainer token at
`GET /v1/runs/{delivery_id}/investigations/{investigation_id}`. The saved report
shows the stable and candidate evidence, the retrieval change, and its result.
An investigation can support a diagnosis or return inconclusive. The release
verdict is recorded separately.

The worker runs this workflow with LangGraph and a scripted local planner. It
can execute one experiment per investigation. To also try a prompt regression,
a stale recorded tool response, and an ambiguous failure, set
`RELEASETRACE_EXTENDED_FIXTURES=1` before starting the API or Compose stack.
Run `python -m service.seed_demo --extended` (via `docker compose exec -T api`
when using Compose), then investigate cases in the new run. This demo requires
no model credentials.
