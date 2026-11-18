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

    classDef process fill:#dbeafe,stroke:#2563eb,color:#172554,stroke-width:1.5px
    classDef decision fill:#fef3c7,stroke:#b45309,color:#451a03,stroke-width:1.5px
    classDef investigation fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:1.5px
    classDef evidence fill:#e2e8f0,stroke:#64748b,color:#0f172a,stroke-width:1.5px
    classDef success fill:#dcfce7,stroke:#15803d,color:#052e16,stroke-width:1.5px

    class Push,Dashboard,API,Worker,Publisher,GitHub process
    class Gate decision
    class Investigator investigation
    class DB,Evidence evidence
    class Argo success
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
    rect rgba(59, 130, 246, 0.12)
        G->>A: Push with delivery ID and signature
        A->>D: Insert scoped delivery and pinned inputs
    end
    alt First delivery
        A-->>G: 202 queued
        W->>D: Claim delivery and save gate report
    else Redelivery
        A-->>G: 202 duplicate
    end
    rect rgba(22, 163, 74, 0.12)
        opt Activity retried after commit
            W->>D: Check delivery state
            D-->>W: Completed result already exists
        end
    end
```

See the [Temporal recovery drill](OPERATIONS.md#run-evaluations-with-temporal) for the test command.

</details>

The [implementation plan](IMPLEMENTATION_PLAN.md) tracks live GitHub verification and deployment of approved OCI images. See the [operations guide](OPERATIONS.md) for configuration.
