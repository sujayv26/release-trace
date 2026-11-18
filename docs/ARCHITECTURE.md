# Architecture

ReleaseTrace serves one configured GitHub repository and installation per API instance. Compose runs PostgreSQL, applies Alembic migrations, and starts FastAPI, the Python worker, and the Next.js dashboard. SQLite supports local service development.

## System design

```mermaid
flowchart TB
    accTitle: ReleaseTrace system design
    accDescr: GitHub and the dashboard connect to the API. Evaluation, investigation, and Check publication read persisted state. The kind canary drill runs independently.
    subgraph Interface["Intake and review"]
        Push["GitHub push"] -->|Signed webhook| API["FastAPI"]
        Dashboard["Next.js dashboard"] <-->|Server-side API calls| API
    end
    API <-->|Jobs, reports, decisions| DB[("PostgreSQL")]
    subgraph Jobs["Background processing"]
        Worker["Worker or Temporal activity"] --> Gate["Deterministic gate"]
        Investigator["LangGraph Investigator"]
        Publisher["Check publisher"]
    end
    DB <-->|Pinned inputs and reports| Worker
    DB <-->|Failed fixture and rerun| Investigator
    DB <-->|Result and Check ID| Publisher
    Publisher -->|Check on evaluated SHA| GitHub["GitHub Checks"]
    subgraph Staging["Independent staging drill"]
        Argo["Argo Rollouts in kind"] --> Evidence["Promotion and abort evidence"]
    end
```

1. The API verifies the signature and project scope, deduplicates by delivery ID, and persists the queued inputs. Build mode snapshots the configured endpoints. Fixture mode records the push SHA as event provenance.
2. The worker evaluates both versions on the same cases and saves the gate result. The optional Temporal worker assigns one workflow ID per queue and delivery and reuses the database transaction to preserve a committed result on retry.
3. The dashboard reads scoped reports. The optional GitHub App publisher writes build results against the evaluated commit and tracks Check IDs and retries.
4. The Investigator reads a saved fixture evaluation, runs up to one allowlisted experiment, and records its diagnosis independently of the release verdict.
5. The API records reviewer decisions bound to the candidate digest. The Argo kind drill separately measures promotion, abort, and stable-service response.

<details>
<summary>Trace a signed push and a retried evaluation</summary>

```mermaid
sequenceDiagram
    accTitle: Delivery deduplication and evaluation recovery
    accDescr: The API persists one queued delivery. A worker saves its report. A retried Temporal activity can reuse the committed report.
    participant G as GitHub
    participant A as API
    participant D as Database
    participant W as Evaluation worker
    G->>A: Push with delivery ID and signature
    A->>D: Insert scoped delivery and pinned inputs
    alt First delivery
        A-->>G: 202 queued
        W->>D: Claim delivery and save gate report
    else Redelivery
        A-->>G: 202 duplicate
    end
    opt Activity retried after commit
        W->>D: Check delivery state
        D-->>W: Completed result already exists
    end
```

See the [Temporal recovery drill](OPERATIONS.md#run-evaluations-with-temporal) for the test command.

</details>

The [implementation plan](IMPLEMENTATION_PLAN.md) tracks live GitHub verification and deployment of approved OCI images. See the [operations guide](OPERATIONS.md) for configuration.
