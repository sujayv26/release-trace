import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from . import api


class RunbookAPITest(unittest.TestCase):
    def test_release_cites_the_source_used_by_ask(self):
        question = {"question": "What is the checkout deployment rollback procedure?"}
        for revision, expected in (("stable", "checkout-rollback-v2"),
                                   ("stale-candidate", "checkout-rollback-v1"),
                                   ("corrected-candidate", "checkout-rollback-v2")):
            token = "t" * 32
            build = {"commit_sha": "a" * 40, "image_digest": "sha256:" + "b" * 64}
            variables = {"RUNBOOK_BUILD_COMMIT": build["commit_sha"],
                         "RUNBOOK_IMAGE_DIGEST": build["image_digest"],
                         "RUNBOOK_ACCESS_TOKEN": token}
            headers = {"Authorization": f"Bearer {token}"}
            with (self.subTest(revision=revision), patch.object(api, "REVISION", revision),
                  patch.dict(os.environ, variables, clear=True), TestClient(api.app) as client):
                response = client.post("/ask", json=question, headers=headers)
                self.assertEqual(response.json()["citations"][0]["id"], expected)
                payload = {"case_id": "rollback", **question, "build": {"commit_sha": "wrong"}}
                self.assertEqual(client.post("/evaluate", json=payload).status_code, 401)
                self.assertEqual(client.post("/evaluate", json=payload, headers=headers).json(),
                                 {"build": build, "citation": expected})
                absent = {"case_id": "absent", "question": "What are the checkout cafeteria lunch prices?"}
                self.assertEqual(client.post("/evaluate", json=absent, headers=headers).json(),
                                 {"build": build, "citation": None})

    def test_release_startup_fails_closed_without_metadata(self):
        with patch.dict(os.environ, {"RUNBOOK_REQUIRE_RELEASE": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "valid build identity"):
                with TestClient(api.app):
                    self.fail("Incomplete release configuration started")
