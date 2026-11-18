# ReleaseTrace

ReleaseTrace is an AI release verification project in development. It compares stable and candidate versions and investigates failed cases through controlled reruns.

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

For a key-free integration check, install `httpx` and run
`python -m unittest service.test_pipeline -v`.

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
