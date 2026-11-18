# ReleaseTrace contributor instructions

Read docs/PRD.md, docs/ARCHITECTURE.md and docs/IMPLEMENTATION_PLAN.md before editing. Consult the design, database, API and investigator guides where relevant. Inspect existing code and preserve working contracts. Implement the requested phase through its normal entry point. Keep the deterministic release gate, investigation agent and rollout controller independent. Validate and scope external inputs, keep secrets out of Git and logs, and preserve key-free local checks. Run the relevant tests and report exact commands, results and blockers. Update the README as runnable workflows land.

Work on the secondary branch and use CI to verify the change before promotion. Merge into main only after the maintainer approves it.
