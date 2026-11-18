"""Provision and revoke individual reviewer credentials and project access."""

import argparse
import os
import re
from hashlib import sha256
from secrets import token_urlsafe
from typing import get_args

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .projects import Role, managed_projects
from .store import accounts, database, project_memberships, project_owners

HANDLE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
ACCESS_KEY = re.compile(r"rt_[A-Za-z0-9_-]{43}")


def key_hash(key):
    return sha256(key.encode("ascii")).hexdigest()


def bind_project_owners(engine, projects, *, managed):
    query = select(project_owners).where(project_owners.c.repository_id.in_(projects))
    intended = {key: project.organization for key, project in projects.items()}
    try:
        with engine.begin() as connection:
            saved = dict(map(tuple, connection.execute(query)))
            if saved and (not managed or any(intended[key] != owner for key, owner in saved.items())):
                raise ValueError("A managed repository cannot change organization or return to shared-token access")
            missing = [{"repository_id": key, "organization": owner}
                       for key, owner in intended.items() if key not in saved]
            if managed and missing:
                connection.execute(project_owners.insert(), missing)
    except IntegrityError:
        with engine.connect() as connection:
            if dict(map(tuple, connection.execute(query))) != intended:
                raise ValueError("Managed repository ownership changed during setup") from None


def issue_key(connection, handle, *, create):
    key = "rt_" + token_urlsafe(32)
    values = {"key_hash": key_hash(key)}
    if create:
        connection.execute(accounts.insert().values(handle=handle, **values))
    else:
        result = connection.execute(accounts.update().where(accounts.c.handle == handle).values(**values))
        if not result.rowcount:
            raise ValueError("Account does not exist")
    return key


def change_membership(connection, handle, project, role):
    if connection.scalar(select(accounts.c.handle).where(accounts.c.handle == handle)) is None:
        raise ValueError("Account does not exist")
    key = {
        "handle": handle, "repository_id": project.repository_id, "installation_id": project.installation_id,
        "organization": project.organization,
    }
    match = tuple(project_memberships.c[column] == value for column, value in key.items())
    if role is None:
        connection.execute(project_memberships.delete().where(*match))
    else:
        changed = connection.execute(project_memberships.update().where(*match).values(role=role))
        if not changed.rowcount:
            connection.execute(project_memberships.insert().values(**key, role=role))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("create", "rotate", "disable", "grant", "revoke"):
        command = actions.add_parser(action)
        command.add_argument("handle")
        if action in ("grant", "revoke"):
            command.add_argument("repository_id", type=int)
        if action == "grant":
            command.add_argument("role", choices=get_args(Role))
    args = parser.parse_args()
    if not HANDLE.fullmatch(args.handle):
        parser.error("Use a lowercase account handle of up to 64 characters")
    directory = os.getenv("RELEASETRACE_PROJECTS_FILE")
    if not directory:
        parser.error("RELEASETRACE_PROJECTS_FILE is required for managed accounts")
    try:
        projects = managed_projects(directory)
        engine = database()
        key = None
        try:
            with engine.begin() as connection:
                if args.action in ("create", "rotate"):
                    key = issue_key(connection, args.handle, create=args.action == "create")
                elif args.action == "disable":
                    result = connection.execute(accounts.update().where(
                        accounts.c.handle == args.handle,
                    ).values(key_hash=None))
                    if not result.rowcount:
                        raise ValueError("Account does not exist")
                else:
                    project = projects.get(args.repository_id)
                    if project is None:
                        raise ValueError("Repository does not exist in the managed project file")
                    change_membership(connection, args.handle, project, getattr(args, "role", None))
        finally:
            engine.dispose()
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    except (KeyError, SQLAlchemyError):
        parser.exit(1, "Could not update managed access. Check the database, migration, and account name.\n")
    if key is not None:
        print(key)


if __name__ == "__main__":
    main()
