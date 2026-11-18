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
