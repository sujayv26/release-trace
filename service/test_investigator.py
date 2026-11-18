import json
import unittest

from demo.release_gate import run_suite

from .evaluate_builds import FIXTURES
from .investigator import investigate


class InvestigatorTest(unittest.TestCase):
    def test_abstains_when_retrieval_swap_does_not_restore_expected_citation(self):
        fixture = json.loads((FIXTURES / "fixture.json").read_bytes())
        suite = json.loads((FIXTURES / "gate_suite.json").read_bytes())
        fixture["required_citation"] = "unavailable-policy"
        report = run_suite(fixture, suite)
        result = investigate({"fixture": fixture, "suite": suite}, report, fixture["case_id"])
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual(result["reason"], "rerun_did_not_establish_cause")
        self.assertEqual(result["experiment"]["outcome"], "fail")
