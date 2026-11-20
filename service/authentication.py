"""Authenticate API keys and manage short-lived browser sessions."""

import hmac
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe

from sqlalchemy import select

from .accounts import ACCESS_KEY, key_hash
from .projects import Role
from .store import accounts, browser_sessions, project_memberships

SESSION_TOKEN = re.compile(r"rts_[A-Za-z0-9_-]{43}")
SESSION_TTL_SECONDS = 60 * 60


@dataclass(frozen=True)
class Principal:
    handle: str | None
    roles: dict[int, Role]


def bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    return token if scheme.lower() == "bearer" else ""


class Authentication:
    def __init__(self, engine, projects, managed, local_tokens):
        self.engine = engine
        self.projects = projects
        self.managed = managed
        self.local_hashes = {role: key_hash(value) for role, value in local_tokens.items()}

    def _principal(self, connection, credential_hash):
        if self.managed:
            rows = connection.execute(select(
                accounts.c.handle, project_memberships.c.repository_id, project_memberships.c.installation_id,
                project_memberships.c.organization, project_memberships.c.role,
            ).outerjoin(project_memberships).where(accounts.c.key_hash == credential_hash)).all()
            if not rows:
                return None
            roles = {}
            for row in rows:
                project = self.projects.get(row.repository_id)
                if project and (project.installation_id, project.organization) == (row.installation_id, row.organization):
                    roles[row.repository_id] = row.role
            return Principal(rows[0].handle, roles)
        for role, expected in self.local_hashes.items():
            if hmac.compare_digest(credential_hash, expected):
                return Principal(None, {next(iter(self.projects)): role})
        return None

    def _key_fingerprint(self, token):
        if self.managed:
            return key_hash(token) if ACCESS_KEY.fullmatch(token) else None
        return key_hash(token) if token and token.isascii() else None

    def authenticate(self, token):
        if SESSION_TOKEN.fullmatch(token):
            with self.engine.connect() as connection:
                credential_hash = connection.scalar(select(browser_sessions.c.credential_hash).where(
                    browser_sessions.c.token_hash == key_hash(token),
                    browser_sessions.c.expires_at > datetime.now(timezone.utc),
                ))
                return self._principal(connection, credential_hash) if credential_hash else None
        credential_hash = self._key_fingerprint(token)
        if credential_hash is None:
            return None
        with self.engine.connect() as connection:
            return self._principal(connection, credential_hash)

    def create_session(self, key):
        if SESSION_TOKEN.fullmatch(key) or (credential_hash := self._key_fingerprint(key)) is None:
            return None
        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            if self._principal(connection, credential_hash) is None:
                return None
            connection.execute(browser_sessions.delete().where(browser_sessions.c.expires_at <= now))
            token = "rts_" + token_urlsafe(32)
            connection.execute(browser_sessions.insert().values(
                token_hash=key_hash(token), credential_hash=credential_hash,
                expires_at=now + timedelta(seconds=SESSION_TTL_SECONDS),
            ))
        return token

    def end_session(self, token):
        if SESSION_TOKEN.fullmatch(token):
            with self.engine.begin() as connection:
                connection.execute(browser_sessions.delete().where(browser_sessions.c.token_hash == key_hash(token)))
