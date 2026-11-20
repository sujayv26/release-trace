# ReleaseTrace file and folder flowcharts

Start with the repository overview, then open the chart for the part you want
to read. Arrows show a call, data flow, configuration relationship, or test
coverage. Where a node contains several filenames, each line explains one file.
See the [system architecture](ARCHITECTURE.md) for trust boundaries and the
complete release sequence.

**Color key.** Purple marks the reviewer experience and documentation, rose
marks access, blue marks requests and services, teal marks stored data and
configuration, amber marks evaluation rules, coral marks retrieval and
investigation, green marks deployment, and slate marks tooling and tests.
Every icon is paired with a text label.

## Repository overview

```mermaid
flowchart LR
    accTitle: ReleaseTrace repository at a glance
    accDescr: The dashboard and GitHub send requests to the service. The service uses PostgreSQL and evaluates Runbook RAG. CI tests the demo, Runbook RAG, and rollout tooling.
    Reviewer["👤 Reviewer"] --> UI["🖥️ dashboard/ · sign in and review evidence"]
    GitHub["📨 GitHub push"] --> API["🔌 service/ · API, auth, queues and jobs"]
    UI <-->|Server-side requests| API
    API <-->|Runs, access, decisions| DB[("🗄️ PostgreSQL")]
    API -->|Fixture cases and shared gate| Demo["⚖️ demo/ · gate, fixtures and pilot drills"]
    API -->|Evaluate pinned builds| RAG["🔎 runbook_rag/ · example application"]
    Staging["🚀 staging/ · operator and canary tools"] <-->|Approval and observation| API
    CI["🧪 .github/workflows/ · automated checks"] --> Demo
    CI --> RAG
    CI --> Staging
    Config["⚙️ Root config · Compose, Render, Alembic"] --> UI
    Config --> API
    Docs["📚 docs/ · contracts and runbooks"] -.-> Config

    classDef review fill:#f3e8ff,stroke:#8b47c5,color:#401967,stroke-width:2px
    class Reviewer,UI,Docs review
    classDef api fill:#e1efff,stroke:#3070c7,color:#15386a,stroke-width:2px
    class GitHub,API api
    classDef data fill:#d9f6f1,stroke:#168176,color:#0d514b,stroke-width:2px
    class DB data
    classDef decision fill:#fff1c7,stroke:#a86e16,color:#5d3b07,stroke-width:2px
    class Demo decision
    classDef research fill:#ffe9df,stroke:#c85f3a,color:#6f2e17,stroke-width:2px
    class RAG research
    classDef release fill:#e5f6d9,stroke:#4e922c,color:#284f19,stroke-width:2px
    class Staging release
    classDef tool fill:#edf1f8,stroke:#667a9a,color:#2d3d57,stroke-width:2px
    class CI,Config tool
```

## Dashboard

Paths in this chart start at `dashboard/`. Bracketed segments in the route
paths are parameters from the URL.

```mermaid
flowchart TB
    Browser["👤 Reviewer browser"] --> Layout["🖼️ app/layout.tsx · shared page shell and sign-out"]
    Layout --> Home["🏠 app/page.tsx · sign-in, projects and release history"]
    Layout --> Run["📋 app/runs/[id]/page.tsx · verdict, evidence, approval and rollout"]
    Run --> Investigation["🔬 app/runs/[id]/investigations/[investigationId]/page.tsx · diagnosis and rerun"]
    Home --> History["🔍 app/run-history.tsx · search and filter the loaded runs"]
    Run --> Shared["🧩 app/components.tsx · status, timestamps and evidence"]
    Layout --> CSS["🎨 app/styles.css · shared layout and visual styling"]
    Home --> Actions["🎛️ app/actions.ts · sign-in, sign-out, approve and investigate"]
    Run --> Actions
    Actions --> Session["🔑 lib/session.ts · exchange key, manage session cookie"]
    Session --> Client["🔌 lib/releases.ts · server-only API client and response types"]
    Run --> Client
    Home --> Client
    Investigation --> Client
    Client --> API["⚡ service/api.py · authenticated API routes"]
    Config["⚙️ next.config.ts · standalone output and security headers<br/>tsconfig.json · TypeScript checks<br/>next-env.d.ts · generated Next.js types"] --> Layout
    Packages["📦 package.json · scripts and dependencies<br/>package-lock.json · resolved dependency versions"] --> Container["🐳 Dockerfile · build and run production Next.js"]
    Container --> Layout
    Guide["📘 AGENTS.md · local Next.js contributor guidance"] -.-> Config

    classDef review fill:#f3e8ff,stroke:#8b47c5,color:#401967,stroke-width:2px
    class Browser,Layout,Home,Run,Investigation,History,Shared,CSS review
    classDef access fill:#ffe9f2,stroke:#be3974,color:#641c41,stroke-width:2px
    class Actions,Session access
    classDef api fill:#e1efff,stroke:#3070c7,color:#15386a,stroke-width:2px
    class Client,API api
    classDef tool fill:#edf1f8,stroke:#667a9a,color:#2d3d57,stroke-width:2px
    class Config,Packages,Container,Guide tool
```

## Service runtime

Paths start at `service/`. The dashboard and webhooks feed the same API, and
background workers and operator tools use its saved records.

```mermaid
flowchart TB
    Requests["📨 Dashboard, GitHub, and operator requests"] --> API["🔌 api.py · HTTP routes, scope checks and approvals"]
    API --> Auth["🔐 authentication.py · key and session validation, live roles"]
    API --> Projects["🗂️ projects.py · managed project and endpoint config"]
    Projects --> Policy["📏 evaluation_config.py · validate and fingerprint suites and policies"]
    Accounts["👥 accounts.py · provision keys and change project grants"] --> Store
    Auth --> Store["🗃️ store.py · SQLAlchemy tables, evaluation queue and report transaction"]
    API --> Store
    Poll["⚙️ worker.py · poll and process background work"] --> Store
    Temporal["⏱️ temporal_worker.py · dispatch and retry evaluation workflows"] --> Store
    Store --> Eval["🧮 evaluate_builds.py · call builds, check identity and citations"]
    Eval --> Gate["⚖️ demo/release_gate.py · deterministic gate"]
    Poll --> Investigator["🔍 investigator.py · queue and save case investigations"]
    API --> Investigator
    Investigator --> Graph["🔬 investigation_graph.py · bounded LangGraph experiment"]
    Bench["📊 investigation_benchmark.py · compare diagnostic approaches"] --> Graph
    Poll --> Publisher["📤 check_queue.py · claim Check work, save ID or retry time"]
    Publisher --> Check["✅ github_check.py · build payload and reconcile GitHub Checks"]
    Health["🩺 queue_health.py · detect stalled evaluations"] --> Store
    Store --> Postgres[("🗄️ PostgreSQL · queue, reports, access and decisions")]
    Seed["🌱 seed_demo.py · enqueue a locally signed demo event"] --> API
    Smoke["🧪 smoke_demo.py · verify evaluation and investigation<br/>smoke_approval.py · verify passing-build approval"] --> API
    Deps["📦 requirements.txt · core Python dependencies<br/>requirements.temporal.txt · optional Temporal dependencies<br/>requirements.compose.lock · exact Compose dependencies"] --> Image["🐳 Dockerfile · package API and worker entry points"]
    Image --> API
    Image --> Poll

    classDef review fill:#f3e8ff,stroke:#8b47c5,color:#401967,stroke-width:2px
    class Requests review
    classDef access fill:#ffe9f2,stroke:#be3974,color:#641c41,stroke-width:2px
    class Auth,Accounts access
    classDef api fill:#e1efff,stroke:#3070c7,color:#15386a,stroke-width:2px
    class API,Poll,Temporal,Publisher,Check api
    classDef data fill:#d9f6f1,stroke:#168176,color:#0d514b,stroke-width:2px
    class Projects,Store,Postgres data
    classDef decision fill:#fff1c7,stroke:#a86e16,color:#5d3b07,stroke-width:2px
    class Policy,Eval,Gate decision
    classDef research fill:#ffe9df,stroke:#c85f3a,color:#6f2e17,stroke-width:2px
    class Investigator,Graph,Bench research
    classDef tool fill:#edf1f8,stroke:#667a9a,color:#2d3d57,stroke-width:2px
    class Health,Seed,Smoke,Deps,Image tool
```

### Service migrations and tests

```mermaid
flowchart TB
    Command["⌨️ python -m service.store"] --> Upgrade["⬆️ schema.py · upgrade or adopt an existing database"]
    Upgrade --> Engine["⚙️ migrations/env.py · run Alembic against the configured database"]
    Template["📝 migrations/script.py.mako · template for new revisions"] -.-> Versions
    Engine --> Versions["🗂️ migrations/versions/ · apply revisions in order"]
    Versions --> Base["🧱 0001_webhook_deliveries.py · initial queue and run table<br/>0002_delivery_timestamps.py · run timestamps<br/>0003_github_check_delivery.py · Check publication state"]
    Base --> Evidence["📋 0004_investigations.py · case rerun reports<br/>0005_approvals.py · digest-bound decisions<br/>0006_deployments.py · observed rollout outcomes"]
    Evidence --> Access["🔐 0007_accounts.py · reviewer accounts and project grants<br/>0008_account_access_events.py · operator audit history<br/>0009_browser_sessions.py · hashed browser sessions"]
    Tests["🧪 service/test_*.py"] --> DataTests["🗄️ test_schema.py · legacy upgrade and preserved reports<br/>test_pipeline.py · API, authorization and release flow<br/>test_project_evaluation.py · scoped and pinned suites"]
    Tests --> LogicTests["🧮 test_evaluate_builds.py · build evidence and destination checks<br/>test_investigator.py · bounded reruns and diagnoses"]
    Tests --> RecoveryTests["🔁 test_github_check.py · Check payload and publishing<br/>test_check_queue.py · scoped retries and reconciliation<br/>test_temporal_worker.py · worker replacement and report recovery"]

    classDef data fill:#d9f6f1,stroke:#168176,color:#0d514b,stroke-width:2px
    class Command,Upgrade,Engine,Template,Versions,Base,Evidence data
    classDef access fill:#ffe9f2,stroke:#be3974,color:#641c41,stroke-width:2px
    class Access access
    classDef tool fill:#edf1f8,stroke:#667a9a,color:#2d3d57,stroke-width:2px
    class Tests,DataTests,LogicTests,RecoveryTests tool
```

## Demo

```mermaid
flowchart TB
    Rules["📏 gate_suite.json · additional cases and gate policy"] --> Gate["⚖️ release_gate.py · shared deterministic gate"]
    Fixture["🧩 fixture.json · controlled support-policy regression"] --> Sim["🎭 release_trace_demo.py · simulate answers and retrieval changes"]
    Sim --> Gate
    Diagnosis["🔬 investigation_cases.json · extended investigation scenarios<br/>investigation_holdout.json · held-out diagnosis benchmark cases"] --> Sim
    Targets["🎯 build_targets.json · blocked demo build pair<br/>passing_build_targets.json · passing demo build pair"] --> HTTP["🔌 build_target.py · serve demo evaluation endpoints"]
    HTTP --> Eval["🧮 service/evaluate_builds.py · call demo targets"]
    Eval --> Gate
    Rehearse["🔁 pilot_rehearsal.py · isolated projects, worker recovery and database restore"] --> Pilot["🌐 pilot_target.py · disposable HTTPS app endpoints"]
    Rehearse --> Browser["🖥️ pilot_browser.py · production dashboard session checks"]
    Unit["🧪 test_release_gate.py · gate verdict tests<br/>test_release_trace_demo.py · simulation and controlled rerun tests"] --> Gate
    Unit --> Sim

    classDef decision fill:#fff1c7,stroke:#a86e16,color:#5d3b07,stroke-width:2px
    class Rules,Gate,Eval decision
    classDef research fill:#ffe9df,stroke:#c85f3a,color:#6f2e17,stroke-width:2px
    class Fixture,Sim,Diagnosis research
    classDef api fill:#e1efff,stroke:#3070c7,color:#15386a,stroke-width:2px
    class Targets,HTTP,Pilot api
    classDef tool fill:#edf1f8,stroke:#667a9a,color:#2d3d57,stroke-width:2px
    class Rehearse,Browser,Unit tool
```

## Runbook RAG

```mermaid
flowchart TB
    Req["💬 Question from a user or ReleaseTrace"] --> API["🔌 api.py · answer, health and evaluation endpoints"]
    API --> Rank["🔎 ranking.py · hybrid search and optional reranking"]
    Rank --> Search["📑 retrieval.py · SQLite FTS5 index, BM25 and baseline answer"]
    Corpus["📚 data/runbooks.json · versioned source documents"] --> Search
    Revision["🏷️ release_revision.txt · stable, stale or corrected build behavior"] --> API
    API --> Result["📌 Cited document or abstention, with build identity for evaluation"]
    Baseline["📋 data/eval.json · starter regression cases"] --> Eval["🧮 evaluate.py · verify starter BM25 behavior"]
    Holdout["🧾 data/holdout.json · held-out retrieval questions"] --> Bench["📊 benchmark.py · compare quality and latency"]
    Suite["⚖️ data/release-suite.json · ReleaseTrace case and gate policy"] --> Rehearse["🔁 rehearse.py · evaluate stable, stale and corrected targets"]
    Runner["🧪 ci-rehearse.sh · build and run three pinned OCI images"] --> Rehearse
    Package["📦 Dockerfile · package the service<br/>__init__.py · Python package marker"] --> API
    Test["✅ test_api.py · citation, abstention and build identity contract"] --> API

    classDef review fill:#f3e8ff,stroke:#8b47c5,color:#401967,stroke-width:2px
    class Req review
    classDef api fill:#e1efff,stroke:#3070c7,color:#15386a,stroke-width:2px
    class API api
    classDef research fill:#ffe9df,stroke:#c85f3a,color:#6f2e17,stroke-width:2px
    class Rank,Search,Bench research
    classDef data fill:#d9f6f1,stroke:#168176,color:#0d514b,stroke-width:2px
    class Corpus,Revision,Baseline,Holdout,Result data
    classDef decision fill:#fff1c7,stroke:#a86e16,color:#5d3b07,stroke-width:2px
    class Suite,Eval decision
    classDef tool fill:#edf1f8,stroke:#667a9a,color:#2d3d57,stroke-width:2px
    class Rehearse,Runner,Package,Test tool
```

## Rollout tooling and GitHub Actions

```mermaid
flowchart TB
    CI["🧪 .github/workflows/ci.yml · builds, tests and pilot evidence"] --> Test["✅ Python tests, dashboard build, browser, PostgreSQL, Temporal and Runbook RAG"]
    Staging["🛠️ .github/workflows/staging.yml · disposable kind cluster and rollout evidence"] --> Drill["🔁 staging/run_drill.py · verify healthy promotion and unhealthy abort"]
    Staging --> Rehearse["🎯 staging/rehearse_approval.py · trace approval to the running image"]
    Drill --> Canary["📄 staging/canary.yaml · baseline canary rehearsal resources"]
    Rehearse --> Approved["📋 staging/approved-canary.yaml · approval-connected canary resources"]
    Build["📦 staging/BuildTarget.Dockerfile · build an evaluated target image"] --> Rehearse
    Rehearse --> Promote["🚀 staging/promote_approved.py · verify, submit and observe exact digest"]
    Promote <-->|Current authorization and observation| API["🔌 service/api.py"]
    Promote --> Argo["🚦 Argo Rollouts · decides promotion or abort"]

    classDef tool fill:#edf1f8,stroke:#667a9a,color:#2d3d57,stroke-width:2px
    class CI,Test,Staging,Drill,Rehearse tool
    classDef data fill:#d9f6f1,stroke:#168176,color:#0d514b,stroke-width:2px
    class Canary,Approved,Build data
    classDef release fill:#e5f6d9,stroke:#4e922c,color:#284f19,stroke-width:2px
    class Promote,Argo release
    classDef api fill:#e1efff,stroke:#3070c7,color:#15386a,stroke-width:2px
    class API api
```

## Root configuration and documentation

```mermaid
flowchart TB
    README["📖 README.md · project introduction and local quick start"] --> Local["🐳 compose.yaml · start the local PostgreSQL stack"]
    Local --> Approval["🧩 compose.approval.yaml · add the passing build pair"]
    Local --> Pilot["🧪 compose.pilot.yaml · add managed projects and isolated restore services"]
    Local --> Alembic["🗄️ alembic.ini · migration configuration"]
    README --> Host["☁️ render.yaml · planned hosted API, dashboard, worker and database"]
    Contribution["📝 AGENTS.md · repository contributor guidance<br/>.gitignore · exclude generated and local files from Git<br/>.dockerignore · exclude unnecessary Docker build inputs"] -.-> Local
    README --> Docs["📚 docs/ · product, design, contracts and operations"]
    Docs --> Design["🎨 PRD.md · product scope<br/>DESIGN.md · reviewer interface conventions<br/>IMPLEMENTATION_PLAN.md · delivered and open phases"]
    Docs --> Contracts["📘 ARCHITECTURE.md · component boundaries and workflow<br/>CODE_MAP.md · file and folder diagrams<br/>API.md · endpoint contracts<br/>DATABASE.md · stored records and migrations<br/>SECURITY.md · credentials and trust boundaries"]
    Docs --> Operations["🛠️ OPERATIONS.md · commands and deployment procedures<br/>PILOT.md · pilot onboarding and recovery drills<br/>ReleaseTrace-Agent-Build-Guide.md · Investigator design and measurements"]

    classDef review fill:#f3e8ff,stroke:#8b47c5,color:#401967,stroke-width:2px
    class README,Docs,Design,Contracts review
    classDef tool fill:#edf1f8,stroke:#667a9a,color:#2d3d57,stroke-width:2px
    class Local,Approval,Pilot,Contribution tool
    classDef data fill:#d9f6f1,stroke:#168176,color:#0d514b,stroke-width:2px
    class Alembic data
    classDef release fill:#e5f6d9,stroke:#4e922c,color:#284f19,stroke-width:2px
    class Host,Operations release
```

The diagrams cover repository source and configuration, plus the two local
dashboard files shown in the IDE. Local dependency, build, and virtual
environment directories are generated by the tools.
