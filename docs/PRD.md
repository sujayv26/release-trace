# Product requirements

ReleaseTrace helps AI application maintainers decide whether a candidate is ready to ship. A reviewer compares the stable and candidate builds on the same cases, inspects failures, requests an investigation and approves the tested build before rollout.

## Scope

- Local MVP: versioned support-policy fixtures, stable/candidate comparison, a critical-case gate and a controlled retrieval rerun.
- Connected release: GitHub check on the evaluated commit, run and evidence views, investigation, and approval bound to the evaluated digest.
- Flagship: durable worker recovery, held-out diagnosis benchmark and staged delivery with an observed canary abort.

Viewers read project evidence, maintainers trigger investigations, and authorized approvers record promotion decisions. Missing required evidence blocks promotion. Track critical detection, false blocks, diagnosis accuracy, abstention, cost and recovery time. Use synthetic policies and fake tool responses for the demo.

The current demo serves one configured project. The [implementation plan](IMPLEMENTATION_PLAN.md) records verified milestones and open integrations. The [README](../README.md) walks through release review and links to the measured canary drill.
