"""Check for stalled evaluations independently of the worker."""

import argparse
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from .store import database, deliveries


def stalled_count(engine, max_age_seconds):
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(deliveries).where(
            deliveries.c.state.in_(("queued", "running")),
            or_(deliveries.c.created_at <= cutoff, deliveries.c.created_at.is_(None)),
        ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-age-seconds", type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.max_age_seconds <= 86400:
        parser.error("--max-age-seconds must be between 1 and 86400")

    try:
        engine = database()
        try:
            count = stalled_count(engine, args.max_age_seconds)
        finally:
            engine.dispose()
    except (KeyError, SQLAlchemyError):
        print(json.dumps({"status": "queue_unavailable"}))
        return 2

    print(json.dumps({
        "status": "stalled" if count else "healthy",
        "stalled_count": count,
        "max_age_seconds": args.max_age_seconds,
    }))
    return int(count > 0)


if __name__ == "__main__":
    raise SystemExit(main())
