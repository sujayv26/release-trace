# Security and access

ReleaseTrace accepts GitHub deliveries and processes release evidence on behalf
of configured projects. The hosted pilot uses managed accounts. Access keys
are individual, project roles are assigned by an operator, and the browser
exchanges a key for a one-hour session. The local Compose demo uses shared role
keys and synthetic records.

## Where credentials are used

| Boundary | Credential and enforcement |
| --- | --- |
| Browser to dashboard | A Secure, HttpOnly, SameSite=Strict cookie in a production build. It carries a random session token, scoped to the dashboard host. The dashboard stops accepting the previous access-key cookie. Users sign in again after upgrading. |
| Dashboard to API | The dashboard sends the short-lived session as a bearer token. The database stores its hash and an expiry. Logout revokes the token when the API is reachable. The dashboard clears the cookie even if the API is temporarily unavailable. |
| Operator or CLI to API | The individual access key remains supported. Its hash is stored in the account record. Rotation or disable invalidates the old key and any sessions issued using it. The API loads current project roles on each authenticated request. |
| GitHub to API | The API verifies the raw-body HMAC signature, checks repository and installation scope, and deduplicates the delivery. This credential is separate from browser sessions. |
| Worker to evaluated application | The worker uses project-scoped bearer tokens and checks the approved HTTPS destinations before sending them in hosted mode. |
| Approval to rollout | The API checks the approver role and candidate digest. The staging operator verifies the approved image and trusted build provenance before applying a rollout under the configured policy. |

Protected API responses and dashboard review pages use `no-store`. Next.js
checks the request origin against the dashboard host when handling server
actions. Keep the forwarded host aligned with the public dashboard hostname.
Managed mode disables the interactive API docs
and the OpenAPI endpoint. Dashboard headers restrict framing and embedding
and keep referrers out of downstream requests.

## Operating an invite-only deployment

- Apply the database migrations before starting the new API and dashboard.
  Provision individual keys with the account CLI and share them privately.
  Revoke the relevant project grants when access changes. Rotate compromised
  keys or disable the affected account.
- Use HTTPS ingress, a private database, separate service secrets, and
  restricted access to deployment and audit logs. Keep the webhook secret,
  evaluation credentials, and reviewer keys out of CI artifacts and URLs.
- Verify from the actual ingress that the dashboard accepts a legitimate
  sign-in, logout revokes the token, browsers reject cross-origin action
  requests, and the API prevents cross-project reads. Configure and verify
  edge rate limits for public ingress and alerting for abnormal failures.
- Use the supported [pilot drills](PILOT.md) to verify recovery, restore, and
  the live GitHub App. Treat backups as sensitive. When recovering a live
  service from a backup, invalidate restored browser sessions before opening
  ingress so logout and expiry state cannot be rolled back. Use the
  [restore procedure](PILOT.md#rehearse-recovery-and-restore).

The [local browser rehearsal](PILOT.md#rehearse-locally-from-a-clean-checkout)
uses production Next.js in an isolated stack. It tests two individual users,
cookie settings, expiry and logout, rejected cross-origin sign-in, and fresh
authorization after operator changes. Keep browser traces, storage dumps,
request bodies, and credential-bearing headers out of logs and artifacts.
Hosted policy and reverse-proxy behavior need their own verification. OIDC and
MFA remain future work. Report private issues to the repository maintainer
without including active credentials or customer evidence.
