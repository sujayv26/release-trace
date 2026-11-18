# Architecture

Start with a Python fixture evaluator and a CLI. Add FastAPI/Pydantic, PostgreSQL with SQLAlchemy/Alembic, a separate worker and a Next.js/TypeScript dashboard once the fixtures work.

GitHub events enter the API, which verifies and persists work. The worker runs stable and candidate builds on an identical dataset. A deterministic policy decides the gate, and the GitHub App publishes the result against the evaluated SHA. LangGraph investigates failed cases through bounded tools that read pinned evidence and schedule isolated reruns. It produces a diagnosis. An optional Temporal worker dispatches saved evaluations with one workflow ID per delivery and reuses the database transaction to deduplicate results. GitHub Actions builds digest-addressed images, and Kubernetes with Helm and Argo Rollouts controls staged delivery. Use Compose locally, kind for drills, and OpenTelemetry and Prometheus for correlated evidence.

Use server-side project authorization, scoped credentials, explicit timeouts, redacted artifacts and idempotent processing. Pin artifact, evaluator and policy versions. Keep the evaluator, agent and rollout controller independent.
