from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from . import api
from .grounding import OpenAIBackend


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

    def test_custom_release_corpus_is_pinned_and_excludes_old_version(self):
        records = [
            {"id": "inventory-v1", "title": "Inventory snapshot", "status": "superseded",
             "excerpt": "For inventory snapshot restoration, overwrite the production database."},
            {"id": "inventory-v2", "title": "Inventory snapshot", "status": "active",
             "excerpt": "For inventory snapshot restoration, run the isolated staging restore."},
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "passages.json"
            raw = json.dumps(records).encode()
            path.write_bytes(raw)
            config = {
                "RUNBOOK_CORPUS_FILE": str(path),
                "RUNBOOK_BUILD_COMMIT": "a" * 40,
                "RUNBOOK_IMAGE_DIGEST": "sha256:" + "b" * 64,
                "RUNBOOK_ACCESS_TOKEN": "t" * 32,
            }
            with patch.dict(os.environ, config, clear=True):
                with self.assertRaisesRegex(RuntimeError, "corpus digest"):
                    with TestClient(api.app):
                        self.fail("Unpinned corpus started")
                os.environ["RUNBOOK_CORPUS_SHA256"] = sha256(raw).hexdigest()
                headers = {"Authorization": "Bearer " + config["RUNBOOK_ACCESS_TOKEN"]}
                question = {"question": "Inventory snapshot restoration"}
                with TestClient(api.app) as client:
                    response = client.post("/ask", json=question, headers=headers).json()
                    self.assertEqual(response["citations"][0]["id"], "inventory-v2")
                path.write_bytes(raw + b" ")
                with self.assertRaisesRegex(RuntimeError, "corpus digest"):
                    with TestClient(api.app):
                        self.fail("Modified corpus started")

    def test_dense_retrieval_rejects_unsupported_quotes_and_handles_provider_failure(self):
        scenarios = (
            ("valid", "How can we revert that change?", "checkout-rollback-v2"),
            ("fabricated", "How can we revert that change?", None),
            ("wrong-source", "How can we revert that change?", None),
            ("abstain", "Checkout deployment rollback", None),
            ("timeout", "Checkout deployment rollback", "checkout-rollback-v2"),
            ("embedding-timeout", "Checkout deployment rollback", "checkout-rollback-v2"),
        )
        passage = "pause traffic promotion and get release-lead approval"
        for mode, question, expected in scenarios:
            with self.subTest(mode=mode):
                selected_calls = []

                def respond(request):
                    body = json.loads(request.content)
                    if request.url.path.endswith("/embeddings"):
                        texts = body["input"]
                        if len(texts) == 1 and mode == "embedding-timeout":
                            raise httpx.ReadTimeout("test timeout", request=request)
                        return httpx.Response(200, json={"data": [
                            {"index": i, "embedding": [1, 0] if len(texts) == 1
                             or "pause traffic promotion" in text else [0, 1]}
                            for i, text in reversed(list(enumerate(texts)))
                        ]})
                    selected_calls.append(body)
                    self.assertFalse(body["store"])
                    self.assertTrue(body["text"]["format"]["strict"])
                    self.assertNotIn("checkout-rollback-v1", body["input"])
                    if mode == "timeout":
                        raise httpx.ReadTimeout("test timeout", request=request)
                    evidence = [] if mode == "abstain" else [{
                        "id": "unknown" if mode == "wrong-source" else "checkout-rollback-v2",
                        "quote": "Resume traffic immediately." if mode == "fabricated" else passage,
                    }]
                    return httpx.Response(200, json={"status": "completed", "output": [
                        {"type": "message", "content": [
                            {"type": "output_text", "text": json.dumps({"evidence": evidence})}
                        ]}
                    ]})

                with httpx.Client(base_url="https://api.openai.com/v1/",
                                  transport=httpx.MockTransport(respond)) as mock_provider:
                    backend = OpenAIBackend(mock_provider, "test-embedding", "test-answer", 0.45)
                    config = {"RUNBOOK_BUILD_COMMIT": "a" * 40,
                              "RUNBOOK_IMAGE_DIGEST": "sha256:" + "b" * 64,
                              "RUNBOOK_ACCESS_TOKEN": "t" * 32}
                    with (patch.dict(os.environ, config, clear=True),
                          patch.object(api, "openai_backend", return_value=backend),
                          TestClient(api.app) as client):
                        headers = {"Authorization": "Bearer " + config["RUNBOOK_ACCESS_TOKEN"]}
                        answer = client.post("/ask", json={"question": question}, headers=headers)
                        self.assertEqual(answer.status_code, 200)
                        result = answer.json()
                        self.assertEqual([doc["id"] for doc in result["citations"]],
                                         [expected] if expected else [])
                        if mode == "valid":
                            self.assertEqual(result["answer"], passage)
                        if expected is None:
                            self.assertIsNone(result["answer"])
                        evaluation = client.post("/evaluate", headers=headers, json={
                            "case_id": "grounded-test", "question": question,
                        })
                        self.assertEqual(evaluation.json()["citation"], expected)
                self.assertEqual(len(selected_calls), 0 if mode == "embedding-timeout" else 2)
