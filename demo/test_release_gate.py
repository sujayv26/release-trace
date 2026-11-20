import json
import unittest
from pathlib import Path

from release_gate import apply_gate, run_suite


class ReleaseGateTest(unittest.TestCase):
    def test_critical_case_blocks_at_passing_aggregate_score(self):
        directory = Path(__file__).parent
        fixture = json.loads((directory / "fixture.json").read_text())
        suite = json.loads((directory / "gate_suite.json").read_text())
        report = run_suite(fixture, suite)
        self.assertEqual(report["baseline"]["verdict"], "pass")
        self.assertEqual(report["candidate"]["score"], 0.8)
        self.assertEqual(report["candidate"]["verdict"], "block")
        self.assertEqual(report["candidate"]["critical_failures"], ["account-closure"])

    def test_missing_evidence_prevents_promotion(self):
        report = apply_gate({"required": True}, {"required": {"outcome": "pass"}}, 0)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertEqual(report["missing_evidence"], ["required"])

    def test_noncritical_failures_below_threshold_block(self):
        evidence = {"outcome": "fail", "evidence": {"id": "policy-v1"}}
        self.assertEqual(apply_gate({"case": False}, {"case": evidence}, 0.8)["verdict"], "block")
