import json
import unittest
from contextlib import nullcontext
from unittest.mock import patch

import httpx

from .evaluate_builds import FIXTURES, BuildTargets, evaluate_builds, evaluate_configured_builds


class EvaluateBuildsTest(unittest.TestCase):
    def setUp(self):
        self.targets = BuildTargets.model_validate_json((FIXTURES / "build_targets.json").read_bytes())
        self.fixture = json.loads((FIXTURES / "fixture.json").read_bytes())
        self.suite = json.loads((FIXTURES / "gate_suite.json").read_bytes())

    def run_evaluation(self, failure_variant=None, failure=None):
        cases = {case["case_id"]: case for case in [self.fixture, *self.suite["cases"]]}
        seen = {"baseline": [], "candidate": []}

        def respond(request):
            variant = "baseline" if request.url.port == 8101 else "candidate"
            target = getattr(self.targets, variant)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["Authorization"], f"Bearer {variant}-token")
            body = json.loads(request.content)
            case = cases[body["case_id"]]
            self.assertEqual(body, {"case_id": case["case_id"], "question": case["question"]})
            seen[variant].append(body)
            build = target.build.model_dump()
            citation = case["required_citation"]
            if variant == failure_variant and case["critical"]:
                if failure == "old_policy":
                    citation = "account-closure-v1"
                elif failure == "wrong_build":
                    build["image_digest"] = self.targets.baseline.build.image_digest
                elif failure == "timeout":
                    raise httpx.ReadTimeout("test timeout", request=request)
                elif failure == "oversized":
                    return httpx.Response(200, content=b" " * 17_000)
            return httpx.Response(200, json={"build": build, "citation": citation})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            report = evaluate_builds(client, self.targets, self.fixture, self.suite, {
                "baseline": "baseline-token", "candidate": "candidate-token",
            })
        self.assertEqual(seen["baseline"], seen["candidate"])
        self.assertEqual(len(seen["baseline"]), 5)
        return report

    def test_critical_regression_blocks_even_at_threshold(self):
        report = self.run_evaluation("candidate", "old_policy")
        self.assertEqual(report["verdict"], "block")
        self.assertEqual(report["baseline"]["verdict"], "pass")
        self.assertEqual(report["candidate"]["score"], 0.8)
        self.assertEqual(report["candidate"]["critical_failures"], ["account-closure"])

    def test_invalid_candidate_evidence_prevents_pass(self):
        for failure, reason in (("wrong_build", "build_mismatch"), ("oversized", "response_too_large")):
            with self.subTest(failure=failure):
                report = self.run_evaluation("candidate", failure)
                self.assertEqual(report["verdict"], "inconclusive")
                self.assertEqual(report["candidate"]["missing_evidence"], ["account-closure"])
                self.assertEqual(report["candidate"]["results"]["account-closure"]["reason"], reason)

    def test_passing_candidate_requires_verified_baseline(self):
        self.assertEqual(self.run_evaluation()["verdict"], "pass")
        report = self.run_evaluation("baseline", "timeout")
        self.assertEqual(report["candidate"]["verdict"], "pass")
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertEqual(report["reason"], "baseline_not_verified")

    def test_restricted_worker_never_contacts_another_projects_destination(self):
        build_targets = self.targets.model_dump(mode="json")
        build_targets["baseline"]["url"] = "https://baseline.example.com/evaluate"
        build_targets["candidate"]["url"] = "https://candidate.example.com/evaluate"
        targets = BuildTargets.model_validate(build_targets)
        cases = {case["case_id"]: case for case in [self.fixture, *self.suite["cases"]]}
        seen = []

        def respond(request):
            seen.append(str(request.url))
            variant = "baseline" if str(request.url) == build_targets["baseline"]["url"] else "candidate"
            case = cases[json.loads(request.content)["case_id"]]
            return httpx.Response(200, json={
                "build": build_targets[variant]["build"], "citation": case["required_citation"],
            })

        allowlist = {
            "98": {"baseline": build_targets["baseline"]["url"], "candidate": "https://old.example.com/evaluate"},
            "99": {"candidate": build_targets["candidate"]["url"]},
        }
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            with (
                patch.dict("os.environ", {
                    "RELEASETRACE_REQUIRE_APPROVED_DESTINATIONS": "1",
                    "RELEASETRACE_APPROVED_EVALUATION_URLS": json.dumps(allowlist),
                }) as env,
                patch("service.evaluate_builds.httpx.Client", return_value=nullcontext(client)),
            ):
                report = evaluate_configured_builds(targets, self.fixture, self.suite, repository_id=98)
                self.assertEqual(report["verdict"], "inconclusive")
                self.assertEqual(set(seen), {build_targets["baseline"]["url"]})
                self.assertEqual({item["reason"] for item in report["candidate"]["results"].values()},
                                 {"destination_not_approved"})
                seen.clear()
                del env["RELEASETRACE_APPROVED_EVALUATION_URLS"]
                report = evaluate_configured_builds(targets, self.fixture, self.suite, repository_id=98)
                self.assertEqual(report["verdict"], "inconclusive")
                self.assertEqual(seen, [])
