# Public checkout walkthrough and private pilot

## Publish the walkthrough

The four-file synthetic export is available on [Sites](https://releasetrace-checkout-demo.excellentplover16.chatgpt.site/). The main [Vercel URL](https://releasetrace-demo.vercel.app/) serves the live account-based dashboard.

From the ReleaseTrace checkout, run these commands with Python 3.12 and SQLite FTS5. Use a fresh output directory. The builder replays the checked-in checkout question against current and stale retrieval indexes and applies the deterministic gate. It copies the dashboard stylesheet and emits only the history and two evidence pages.

```sh
python -m demo.public_site public-export
python -m http.server --bind 127.0.0.1 --directory public-export 8080
```

To serve the synthetic walkthrough independently, publish the generated four files on a static host.

Install Playwright and select an installed Chromium browser using `RELEASETRACE_TEST_BROWSER_CHANNEL` (CI uses `chrome`). Run `python -m demo.public_browser --url https://YOUR-DEMO-HOST/`. Verify the blocked and corrected citations, the private-project read denial, and the rejected approval POST. This host needs no connection to ReleaseTrace.

## Deploy the live tracker

Deploy `dashboard/` as a Next.js project on Vercel. Its `vercel.json` selects live tracking. The regular Compose dashboard continues to use the existing reviewer API. Attach a **private** Vercel Blob store in Production. Personal workspace keys and one-hour session tokens are random 256-bit credentials. The private store addresses both by hash. Logout deletes the server-side session, and the account retains its repository IDs. Concurrent list changes use ETags. No GitHub credential is used for public-only workspace lookups.

For optional GitHub sign-in, configure a GitHub App with a callback of `https://YOUR-HOST/auth/callback`, read permissions for Actions, Contents and Checks, and installation on selected repositories. Set `RT_GITHUB_CLIENT_ID`, `RT_GITHUB_CLIENT_SECRET`, `RT_GITHUB_APP_SLUG`, `RT_TRACKER_ORIGIN` and a base64-encoded random 32-byte `RT_TRACKER_ENCRYPTION_KEY` in Vercel. The OAuth exchange uses state and PKCE. The server encrypts the GitHub user token before storing it with the short-lived session. Each private read uses the signed-in user's GitHub token. Workspace-key and GitHub identities have independent saved lists.

Run `python -m demo.tracker_browser https://YOUR-HOST/` with Playwright and an installed browser channel to exercise two separate personal workspaces, cross-account isolation, a real repository, logout, and sign-in again. This creates two test workspaces and removes the saved test repository. Keep keys, cookies and browser traces out of artifacts.

## Connect the controlled GitHub App

1. In GitHub **Settings → Developer settings → GitHub Apps**, configure the pilot App to subscribe to **Push**. Grant repository **Contents: read** and **Checks: write**. Set the webhook URL to the private pilot API's public HTTPS ingress at `/webhooks/github`. Generate a webhook secret and enter it in GitHub and the API secret store as `RELEASETRACE_WEBHOOK_SECRET`. Install the App for **Only select repositories**, choosing `sujayv26/releasetrace-pilot-target`. Record its App ID and installation ID.
2. Use the configured persistent database for the API and worker. Run `python -m service.store` before starting either. Mount the App PEM as a worker-only secret file. Set `RELEASETRACE_GITHUB_APP_ID` and `RELEASETRACE_GITHUB_APP_KEY_FILE` on that worker. Start it with `python -m service.worker --publish-checks`.
3. Mount the same `projects.json`, targets file and a copy of `runbook_rag/data/release-suite.json` for both services. Set `RELEASETRACE_PROJECTS_FILE` to the absolute project directory file path. Use repository ID **1465344349**, the real installation ID and `github_repository` equal to `sujayv26/releasetrace-pilot-target`. [Project file example](OPERATIONS.md#configure-managed-projects-and-reviewer-accounts)
4. Build the stable and proposed commits from the companion repo, push their images to an authorized registry, and deploy their immutable digest references. Set `RUNBOOK_BUILD_COMMIT` and `RUNBOOK_IMAGE_DIGEST` from the build and deployment records. Supply separate `RUNBOOK_ACCESS_TOKEN` secrets. Limit candidate traffic to evaluation. Put the two exact HTTPS evaluation URLs and build identities in the targets file. On the worker, set `RELEASETRACE_REQUIRE_APPROVED_DESTINATIONS=1`, configure `RELEASETRACE_APPROVED_EVALUATION_URLS` for that repository ID, and set `RELEASETRACE_BASELINE_EVALUATION_TOKEN_1465344349` and `RELEASETRACE_CANDIDATE_EVALUATION_TOKEN_1465344349` from the respective target secrets. [Allowlist format](PILOT.md#prepare-the-hosted-environment)
5. Keep `pilot` at the stable build. The two pilot PRs already contain the tested commits. After installing the App, stage the stale configuration and restart services that cache targets. From the companion checkout, push the exact PR head to a new branch with `git push origin build/stale-checkout:refs/heads/verify/stale-checkout`. GitHub associates the Check with that SHA on the existing PR. Record the genuine push delivery GUID, run ID, both build identities, Check URL, App ID, conclusion, and head SHA. Redeliver using the App's **Recent deliveries**. Require HTTP 202 with `duplicate`, one completed evaluation, the same Check ID and exactly one named Check from that App on the SHA. Repeat for `build/corrected-checkout:refs/heads/verify/corrected-checkout` after deploying the corrected target. Keep raw webhooks and credential-bearing headers out of evidence. [Live redelivery procedure](OPERATIONS.md#verify-live-github-app-redelivery)
6. After the first verified live Check, configure an active required status check for **only** `pilot`. Require `ReleaseTrace / Build evaluation` and select the pilot GitHub App as its source. From a collaborator account with merge permission and no bypass, record both merge-box results and PR URLs. Keep the stale PR blocked. The corrected PR should satisfy the ReleaseTrace requirement. Verify the repository's GitHub plan supports private-branch enforcement. [GitHub branch protection availability](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

## Operator checks

### Measured on the local Docker pilot

On November 20, 2026, 20/20 suites completed. Queue wait p95 was **1,290.854 ms** and evaluation p95 was **20.977 ms** for this burst. The independent stalled probe reported one delivery, then zero after recovery. The worker recovered in **12.125 s** from detection through verification. PostgreSQL restored the isolated pilot dump in **0.688 s**. Release evidence, approval, rollout observation and all eight access events matched. A restored session received HTTP 401. The browser checks recorded cross-project reads of 404 in both directions and 403 for a viewer approval. These measurements cover a single local worker and one short case per suite.

### Companion builds verified locally

Use Docker with its containerd image store. In a companion clone, create local branches tracking `origin/build/stale-checkout` and `origin/build/corrected-checkout` alongside `pilot`. From ReleaseTrace run `python -m runbook_rag.pilot_images PATH_TO_COMPANION_CHECKOUT`. The rehearsal builds Git archives, starts the actual images by local digest on loopback, checks HTTP build identity and citations, and verifies credential denial and abstention. The table records the digests from one run. Save the actual registry digests again when deploying.

| Role | Companion commit | Local OCI digest | Result |
| --- | --- | --- | --- |
| Stable | `24adf08d40a288c7d0d964f99560f7758cf2f482` | `sha256:fc438cdc1bc29041e7f4e8ea9a066de9af856911eaf1516fada76bb26050cb87` | Current v2 |
| Stale | `07fdc788bc8c0592ab31b0d6c4bcbfc2c4aa86bb` | `sha256:207aa1d5d765675e2c7ca312e528e229d0ce9deff25355a9f74a094814b49436` | Block, v1 |
| Corrected | `e171d6e329795c7a0ea224cf29048d1276f6cdaf` | `sha256:e6f25e1ddb2aab3fdd1ff7a99834b3ba83e07ec33ccfee056072c5fa787f24a1` | Pass, v2 |

The two private PRs are open for the live test. App delivery, redelivery, required-Check enforcement and non-bypass merge-box results are pending. [Stale PR](https://github.com/sujayv26/releasetrace-pilot-target/pull/1) · [Corrected PR](https://github.com/sujayv26/releasetrace-pilot-target/pull/2)

### Repeat the checks

- At the actual HTTPS ingress, follow the two-account browser checks and the per-route limit table. Record the burst sizes, response codes, recovery after the rate window, successful normal webhook delivery and duplicate redelivery. Test direct origin access. [Ingress settings](PILOT.md#configure-public-ingress-limits)
- Run `python -m demo.pilot_rehearsal --browser --evidence pilot-evidence.json` for the isolated Compose drill. The burst sends 20 one-case suites across two projects with eight concurrent senders and one worker. Each suite calls both HTTPS fixture builds. Queue wait is enqueue to successful claim. Evaluation time covers the worker's evaluation before it persists the report. p95 is the nineteenth sorted sample. Measure the same workload on the deployed system before setting a service objective.
- During a scoped interruption, monitor `python -m service.queue_health` independently, record the alert and its clearance, and verify one completed result after worker recovery. During restore, time `pg_restore`, compare the release, approval, rollout and account-audit records, then invalidate restored sessions while ingress is closed. Require a copied token to return 401 and a fresh authorized session to work. [Recovery procedure](PILOT.md#rehearse-recovery-and-restore)

## Reproduce the archive benchmark

`python -m service.citation_benchmark` runs eight one-case releases through the HTTP evaluator using deterministic response fixtures. The municipal archive policies and fixed answer-quality labels are separate from the checkout corpus. The scorer reads the labels after evaluating each candidate. These are authored synthetic labels without an independent human adjudication. No model or external service is called.

| Category | Cases | Observed gate outcome |
| --- | ---: | --- |
| Superseded loan policy | 1 | Blocked |
| Reading-room policy used for an external loan | 1 | Blocked |
| Publication advice with missing evidence | 1 | Inconclusive, promotion stopped |
| Wrong loan duration or permission giver with the correct citation | 2 | Both passed |
| Valid TIFF checklist with an equivalent source ID | 1 | Blocked |
| Correct advice with the expected source ID | 2 | Both passed |

The gate stopped **3/5 regressions**, missed **2/5**, and incorrectly blocked **1/3 valid releases**. Its verdict checks build identity and the required citation ID. It does not grade answer correctness. The small fixed suite measures these failure modes, with no claim about their frequency in production.
