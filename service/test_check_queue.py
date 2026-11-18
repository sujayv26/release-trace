import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine, select, update

from .check_queue import GitHubApp, publish_one
from .github_check import GITHUB_API
from .schema import upgrade_database
from .store import deliveries


class CheckQueueTest(unittest.TestCase):
    def test_retry_reconciles_check_after_lost_create_response(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            database_url = os.getenv("RELEASETRACE_TEST_DATABASE_URL", f"sqlite:///{Path(directory, 'checks.db').as_posix()}")
            engine = create_engine(database_url)
            cleanup.callback(engine.dispose)
            upgrade_database(engine)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            app = GitHubApp(80, 81, 82, "example/release-trace", key)
            sha = "a" * 40
            with engine.begin() as connection:
                connection.execute(deliveries.insert(), [
                    {
                        "id": delivery_id, "repository_id": app.repository_id, "installation_id": installation,
                        "commit_sha": sha, "state": "completed", "inputs": {"evaluation_mode": mode},
                        "report": {
                            "evaluation_mode": mode, "verdict": "block",
                            "builds": {"candidate": {"commit_sha": sha, "image_digest": "sha256:" + "b" * 64}},
                            "candidate": {"verdict": "block", "score": .8, "critical_failures": ["account-closure"], "missing_evidence": []},
                        },
                    }
                    for delivery_id, installation, mode in (
                        ("check-1", app.installation_id, "build_endpoints"),
                        ("check-outside", 99, "build_endpoints"),
                        ("check-fixture", app.installation_id, "synthetic_fixture"),
                        ("demo-check", app.installation_id, "build_endpoints"),
                    )
                ])

            created = []
            repo = "/repos/example/release-trace"

            def respond(request):
                authorization = request.headers["Authorization"]
                if request.url.path == "/app/installations/81/access_tokens":
                    self.assertEqual(request.method, "POST")
                    self.assertEqual(json.loads(request.content), {
                        "repository_ids": [82], "permissions": {"checks": "write"},
                    })
                    claims = jwt.decode(authorization.removeprefix("Bearer "), key.public_key(), algorithms=["RS256"])
                    self.assertEqual(claims["iss"], "80")
                    return httpx.Response(201, json={"token": "test-installation-token"})
                self.assertEqual(authorization, "Bearer test-installation-token")
                if request.url.path == repo:
                    return httpx.Response(200, json={"id": 82})
                if request.url.path == f"{repo}/commits/{sha}/check-runs":
                    return httpx.Response(200, json={"check_runs": created})
                self.assertEqual(request.method, "POST")
                self.assertEqual(request.url.path, f"{repo}/check-runs")
                payload = json.loads(request.content)
                self.assertEqual(payload["external_id"], "check-1")
                self.assertEqual(payload["conclusion"], "failure")
                created.append({**payload, "id": 100, "app": {"id": app.app_id}})
                raise httpx.ReadTimeout("test timeout", request=request)

            primary = deliveries.c.id == "check-1"
            with httpx.Client(base_url=GITHUB_API, transport=httpx.MockTransport(respond)) as client:
                with self.assertLogs("service.check_queue", level="WARNING"):
                    self.assertTrue(publish_one(engine, app, client))
                with engine.begin() as connection:
                    run = connection.execute(select(deliveries).where(primary)).one()
                    self.assertIsNone(run.github_check_id)
                    self.assertIsNotNone(run.github_check_retry_at)
                self.assertFalse(publish_one(engine, app, client))
                with engine.begin() as connection:
                    connection.execute(update(deliveries).where(primary).values(
                        github_check_retry_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                    ))
                self.assertTrue(publish_one(engine, app, client))
                self.assertFalse(publish_one(engine, app, client))
            self.assertEqual(len(created), 1)
            with engine.connect() as connection:
                run = connection.execute(select(deliveries).where(primary)).one()
                self.assertEqual(run.github_check_id, 100)
                self.assertIsNone(run.github_check_retry_at)
