"""Publish completed build verdicts with GitHub App installation credentials."""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import time

import httpx
import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from sqlalchemy import or_, select, update

from .github_check import GITHUB_API, GITHUB_HEADERS, check_payload, publish_check
from .store import deliveries

logger = logging.getLogger(__name__)
RETRY_DELAY = timedelta(minutes=1)


@dataclass(frozen=True)
class GitHubApp:
    app_id: int
    installation_id: int
    repository_id: int
    repository: str
    key: RSAPrivateKey = field(repr=False)

    @classmethod
    def from_environment(cls):
        key_file = Path(os.environ["RELEASETRACE_GITHUB_APP_KEY_FILE"])
        key = load_pem_private_key(key_file.read_bytes(), password=None)
        if not isinstance(key, RSAPrivateKey):
            raise ValueError("Expected an RSA private key")
        ids = tuple(int(os.environ[name]) for name in (
            "RELEASETRACE_GITHUB_APP_ID", "RELEASETRACE_INSTALLATION_ID", "RELEASETRACE_REPOSITORY_ID",
        ))
        if any(value <= 0 for value in ids):
            raise ValueError("Expected positive GitHub identifiers")
        return cls(*ids, os.environ["RELEASETRACE_GITHUB_REPOSITORY"], key)

    def installation_token(self, client):
        now = int(time())
        assertion = jwt.encode({"iss": str(self.app_id), "iat": now - 60, "exp": now + 540}, self.key, algorithm="RS256")
        response = client.post(
            f"/app/installations/{self.installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {assertion}"},
            json={"repository_ids": [self.repository_id], "permissions": {"checks": "write"}},
        ).raise_for_status()
        token = response.json()["token"]
        if not isinstance(token, str) or not token:
            raise ValueError("Invalid GitHub installation token response")
        return token


def publish_one(engine, app, client):
    now = datetime.now(timezone.utc)
    eligible = (
        deliveries.c.repository_id == app.repository_id,
        deliveries.c.installation_id == app.installation_id,
        deliveries.c.state == "completed",
        deliveries.c.inputs["evaluation_mode"].as_string() == "build_endpoints",
        deliveries.c.id.notlike("demo-%"),
        deliveries.c.github_check_id.is_(None),
        or_(deliveries.c.github_check_retry_at.is_(None), deliveries.c.github_check_retry_at <= now),
    )
    with engine.begin() as connection:
        run = connection.execute(select(
            deliveries.c.id, deliveries.c.commit_sha, deliveries.c.report,
        ).where(*eligible).order_by(
            deliveries.c.github_check_retry_at.asc().nulls_first(), deliveries.c.id,
        ).limit(1).with_for_update(skip_locked=True)).one_or_none()
        if run is None:
            return False

        # The write serializes publishers on SQLite. PostgreSQL already holds
        # a row lock. Commit the check ID or retry time with the claim.
        claimed = connection.execute(update(deliveries).where(
            *eligible, deliveries.c.id == run.id,
        ).values(github_check_retry_at=now + RETRY_DELAY))
        if claimed.rowcount != 1:
            return False

        try:
            payload = check_payload(run)
            if payload["conclusion"] == "neutral":
                raise ValueError("Expected a saved build verdict")
            client.headers["Authorization"] = f"Bearer {app.installation_token(client)}"
            result = publish_check(client, app.repository, app.repository_id, app.app_id, payload)
            check_id = result["check_id"]
            if type(check_id) is not int or check_id <= 0:
                raise ValueError("Invalid GitHub check ID")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            retry_at = datetime.now(timezone.utc) + RETRY_DELAY
            if isinstance(error, httpx.HTTPStatusError):
                after = error.response.headers.get("Retry-After", "")
                if after.isascii() and after.isdecimal() and len(after) <= 8:
                    retry_at = max(retry_at, now + timedelta(seconds=int(after)))
            connection.execute(update(deliveries).where(deliveries.c.id == run.id).values(
                github_check_retry_at=retry_at,
            ))
            logger.warning("GitHub Check pending for %s (%s)", run.id, type(error).__name__)
        else:
            connection.execute(update(deliveries).where(deliveries.c.id == run.id).values(
                github_check_id=check_id, github_check_retry_at=None,
            ))
        finally:
            client.headers.pop("Authorization", None)
        return True


def github_client():
    return httpx.Client(base_url=GITHUB_API, headers=GITHUB_HEADERS, timeout=10, follow_redirects=False)
