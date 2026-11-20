import json
import unittest

import httpx

from .github_check import CHECK_NAME, publish_check


class GitHubCheckTest(unittest.TestCase):
    def test_second_publish_finds_existing_check(self):
        payload = {"name": CHECK_NAME, "head_sha": "a" * 40, "external_id": "delivery-1"}
        saved = {**payload, "id": 91, "app": {"id": 10}}
        created = []
        repo = "/repos/example/release-trace"

        def respond(request):
            if request.url.path == repo:
                return httpx.Response(200, json={"id": 1})
            if request.url.path == f"{repo}/commits/{payload['head_sha']}/check-runs":
                self.assertEqual(request.url.params["app_id"], "10")
                self.assertEqual(request.url.params["filter"], "all")
                return httpx.Response(200, json={"check_runs": created})
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, f"{repo}/check-runs")
            self.assertEqual(json.loads(request.content), payload)
            created.append(saved)
            return httpx.Response(201, json=saved)

        with httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(respond)) as client:
            result = publish_check(client, "example/release-trace", 1, 10, payload)
            self.assertEqual(result, {"status": "published", "check_id": 91})
            result = publish_check(client, "example/release-trace", 1, 10, payload)
            self.assertEqual(result, {"status": "existing", "check_id": 91})
            with self.assertRaisesRegex(ValueError, "does not match"):
                publish_check(client, "example/release-trace", 2, 10, payload)
        self.assertEqual(created, [saved])
