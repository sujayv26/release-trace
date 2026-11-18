import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine, select, update

from . import worker
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

    def test_managed_worker_publishes_only_configured_repository_scopes(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            root = Path(directory)
            url = os.getenv("RELEASETRACE_TEST_DATABASE_URL", f"sqlite:///{(root / 'checks.db').as_posix()}")
            engine = create_engine(url)
            cleanup.callback(engine.dispose)
            upgrade_database(engine)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            key_file = root / "app.pem"
            key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
            scopes = {"alpha": (8101, 9101, "a" * 40), "beta": (8102, 9102, "b" * 40)}
            directory_file = root / "projects.json"
            directory_file.write_text(json.dumps({"projects": [
                {"repository_id": repository, "installation_id": installation, "organization": name,
                 "name": name, "github_repository": f"example/{name}"}
                for name, (repository, installation, _) in scopes.items()
            ] + [{"repository_id": 8103, "installation_id": 9103, "organization": "extra", "name": "Extra"}]}),
                encoding="utf-8")

            def delivery(name, repository, installation, sha):
                return {"id": f"managed-check-{name}", "repository_id": repository, "installation_id": installation,
                        "commit_sha": sha, "state": "completed", "inputs": {"evaluation_mode": "build_endpoints"},
                        "report": {"evaluation_mode": "build_endpoints", "verdict": "pass",
                                   "builds": {"candidate": {"commit_sha": sha, "image_digest": "sha256:" + "a" * 64}},
                                   "candidate": {"score": 1, "critical_failures": [], "missing_evidence": []}}}

            with engine.begin() as connection:
                connection.execute(deliveries.insert(), [delivery(name, *scope) for name, scope in scopes.items()] + [
                    delivery("other-installation", 8101, 9199, "c" * 40), delivery("disabled", 8103, 9103, "d" * 40),
                ])
            published = []

            def respond(request):
                path = request.url.path
                token = request.headers["Authorization"].removeprefix("Bearer ")
                if path.startswith("/app/installations/"):
                    installation = int(path.split("/")[3])
                    name, (repository, _, _) = next(item for item in scopes.items() if item[1][1] == installation)
                    self.assertEqual(jwt.decode(token, key.public_key(), algorithms=["RS256"])["iss"], "80")
                    self.assertEqual(json.loads(request.content), {"repository_ids": [repository],
                                                                   "permissions": {"checks": "write"}})
                    return httpx.Response(201, json={"token": f"token-{name}"})
                name = path.split("/")[3]
                repository, _, sha = scopes[name]
                self.assertEqual(token, f"token-{name}")
                prefix = f"/repos/example/{name}"
                if path == prefix:
                    return httpx.Response(200, json={"id": repository})
                if path == f"{prefix}/commits/{sha}/check-runs":
                    return httpx.Response(200, json={"check_runs": []})
                self.assertEqual((request.method, path), ("POST", f"{prefix}/check-runs"))
                payload = json.loads(request.content)
                self.assertEqual((payload["external_id"], payload["head_sha"]), (f"managed-check-{name}", sha))
                published.append(name)
                return httpx.Response(201, json={"id": repository + 100})

            env = {"RELEASETRACE_DATABASE_URL": url, "RELEASETRACE_PROJECTS_FILE": str(directory_file),
                   "RELEASETRACE_GITHUB_APP_KEY_FILE": str(key_file), "RELEASETRACE_GITHUB_APP_ID": "80"}
            with (
                patch.dict(os.environ, env),
                patch("sys.argv", ["worker", "--once", "--publish-checks"]),
                patch("service.check_queue.github_client", return_value=httpx.Client(
                    base_url=GITHUB_API, transport=httpx.MockTransport(respond))),
            ):
                worker.main()
            self.assertEqual(published, ["alpha", "beta"])
            with engine.connect() as connection:
                rows = connection.execute(select(deliveries.c.id, deliveries.c.github_check_id).where(
                    deliveries.c.id.startswith("managed-check-"),
                ))
                checks = {delivery_id: check_id for delivery_id, check_id in rows}
            self.assertEqual(checks, {"managed-check-alpha": 8201, "managed-check-beta": 8202,
                                      "managed-check-other-installation": None, "managed-check-disabled": None})
