"""Queue and run the controlled retrieval investigation on saved fixtures."""

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from demo.release_trace_demo import Config, digest

from .investigation_graph import MAX_EXPERIMENTS, RESTORE_RETRIEVAL, InvestigationContext, run_graph, scripted_planner
from .store import deliveries, investigations


def investigation_key(run_id, case_id):
    return digest([run_id, case_id, RESTORE_RETRIEVAL])


def enqueue_investigation(engine, run_id, case_id):
    investigation_id = investigation_key(run_id, case_id)
    try:
        with engine.begin() as connection:
            connection.execute(investigations.insert().values(
                id=investigation_id, run_id=run_id, case_id=case_id, state="queued",
            ))
    except IntegrityError:
        with engine.connect() as connection:
            exists = connection.scalar(select(investigations.c.id).where(investigations.c.id == investigation_id))
        if exists is None:
            raise
    return investigation_id


def investigate(inputs, report, case_id, *, planner=scripted_planner, experiment_budget=MAX_EXPERIMENTS):
    fixture = inputs["fixture"]
    case = next(item for item in [fixture, *inputs["suite"]["cases"]] if item["case_id"] == case_id)
    baseline = report["baseline"]["results"][case_id]
    candidate = report["candidate"]["results"][case_id]
    stable_config, candidate_config = (Config(**fixture[name]) for name in ("baseline", "candidate"))
    result = {
        "outcome": "inconclusive", "experiment": None,
        "required_citation": case["required_citation"], "baseline": baseline, "candidate": candidate,
        "dataset_digest": report["dataset_digest"], "policy_digest": report["policy_digest"],
    }
    return run_graph(result, InvestigationContext(case, stable_config, candidate_config, planner, experiment_budget))


def process_investigation(engine):
    with engine.begin() as connection:
        job = connection.execute(select(investigations).where(
            investigations.c.state == "queued",
        ).order_by(investigations.c.id).limit(1).with_for_update(skip_locked=True)).one_or_none()
        if job is None:
            return None
        claim = connection.execute(update(investigations).where(
            investigations.c.id == job.id, investigations.c.state == "queued",
        ).values(state="running"))
        if claim.rowcount != 1:
            return None
        run = connection.execute(select(deliveries.c.inputs, deliveries.c.report).where(
            deliveries.c.id == job.run_id,
        )).one()
        result = investigate(run.inputs, run.report, job.case_id)
        connection.execute(update(investigations).where(investigations.c.id == job.id).values(
            state="completed", report=result,
        ))
        return job.id
