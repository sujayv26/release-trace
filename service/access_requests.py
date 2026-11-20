"""Accept private viewer requests and let a named operator decide them atomically."""
import argparse
import json
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .accounts import HANDLE
from .projects import managed_projects
from .store import access_requests as queue, account_access_events, accounts, database, project_memberships


def submit(connection, handle, repository_id, project):
    # Serialize same-account submissions, including the pending limit and first insertion.
    active = connection.execute(accounts.update().where(accounts.c.handle == handle, accounts.c.key_hash.is_not(None))
                                .values(key_hash=accounts.c.key_hash))
    if not active.rowcount:
        raise ValueError("Account unavailable")
    match = (queue.c.handle == handle, queue.c.repository_id == repository_id)
    prior = connection.execute(select(queue).where(*match)).mappings().first()
    if prior and prior["status"] == "pending":
        return
    pending = connection.scalar(select(func.count()).select_from(queue).where(queue.c.handle == handle,
                                                                              queue.c.status == "pending"))
    if pending >= 10:
        raise ValueError("Pending request limit reached")
    # Unknown IDs receive the same acknowledgement. Only the operator sees the directory binding.
    values = dict(id=str(uuid4()), installation_id=project.installation_id if project else None,
                  organization=project.organization if project else None, status="pending",
                  updated_at=datetime.now(timezone.utc))
    if prior:
        connection.execute(queue.update().where(*match).values(**values))
    else:
        connection.execute(queue.insert().values(handle=handle, repository_id=repository_id, **values))


def decide(connection, request_id, approve, operator, projects):
    row = connection.execute(select(queue).where(queue.c.id == request_id)).mappings().first()
    status = "approved" if approve else "denied"
    if not row:
        raise ValueError("Unknown request ID")
    if row["status"] == status:
        return
    now = datetime.now(timezone.utc)
    result = connection.execute(queue.update().where(queue.c.id == request_id, queue.c.status == "pending")
                                .values(status=status, updated_at=now))
    if not result.rowcount:
        raise ValueError("Request already decided")
    if approve:
        project = projects.get(row["repository_id"])
        if not project or (project.installation_id, project.organization) != (row["installation_id"], row["organization"]):
            raise ValueError("Requested project is unavailable or its installation changed")
        if not connection.scalar(select(accounts.c.key_hash).where(accounts.c.handle == row["handle"])):
            raise ValueError("Account is disabled")
        scope = {col: row[col] for col in ("handle", "repository_id", "organization", "installation_id")}
        existing = connection.scalar(select(project_memberships.c.role).filter_by(**scope))
        if existing is None:
            connection.execute(project_memberships.insert().values(**scope, role="viewer"))
    connection.execute(account_access_events.insert().values(
        action="allow" if approve else "deny", handle=row["handle"], operator=operator,
        repository_id=row["repository_id"], installation_id=row["installation_id"], organization=row["organization"],
        role="viewer" if approve else None, recorded_at=now))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for action in ("approve", "deny"):
        command = commands.add_parser(action)
        command.add_argument("request_id", type=UUID)
        command.add_argument("--operator", required=True)
    args = parser.parse_args()
    engine = database()
    try:
        with engine.begin() as connection:
            if args.command == "list":
                rows = connection.execute(select(queue.c.id, queue.c.handle, queue.c.repository_id,
                    queue.c.organization, queue.c.installation_id).where(queue.c.status == "pending")
                    .order_by(queue.c.updated_at, queue.c.id).limit(100)).mappings()
                print(json.dumps([dict(row) for row in rows]))
            else:
                if not HANDLE.fullmatch(args.operator):
                    parser.error("Invalid operator handle")
                projects = managed_projects(os.environ["RELEASETRACE_PROJECTS_FILE"]) if args.command == "approve" else {}
                decide(connection, str(args.request_id), args.command == "approve", args.operator, projects)
    except (ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")
    except SQLAlchemyError:
        parser.exit(1, "Access request storage unavailable. Verify the migration and retry.\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
