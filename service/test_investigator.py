import json
import unittest
from unittest.mock import Mock, patch

from demo.release_gate import run_suite

from .evaluate_builds import FIXTURES
from .investigator import investigate


class InvestigatorTest(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((FIXTURES / "fixture.json").read_bytes())
        self.suite = json.loads((FIXTURES / "gate_suite.json").read_bytes())

    def run_case(self, case_id=None, **kwargs):
        report = run_suite(self.fixture, self.suite)
        return investigate({"fixture": self.fixture, "suite": self.suite}, report, case_id or self.fixture["case_id"], **kwargs)

    def test_abstains_when_retrieval_swap_does_not_restore_expected_citation(self):
        self.fixture["required_citation"] = "unavailable-policy"
        result = self.run_case()
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual(result["reason"], "rerun_did_not_establish_cause")
        self.assertEqual(result["experiment"]["outcome"], "fail")

    def test_exhausted_budget_skips_planning_and_rerun(self):
        planner = Mock(return_value="restore_retrieval")
        with patch("service.investigation_graph.controlled_experiment") as experiment:
            result = self.run_case(planner=planner, experiment_budget=0)
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual(result["reason"], "experiment_budget_exhausted")
        self.assertIsNone(result["experiment"])
        planner.assert_not_called()
        experiment.assert_not_called()

    def test_unsupported_or_timed_out_planner_cannot_trigger_a_rerun(self):
        for planner, reason in (
            (Mock(return_value="restore_prompt"), "unsupported_intervention"),  # Prompt is unchanged in this case.
            (Mock(return_value="unlisted_action"), "unsupported_intervention"),
            (Mock(side_effect=TimeoutError), "planner_timeout"),
        ):
            with self.subTest(reason=reason):
                with patch("service.investigation_graph.controlled_experiment") as experiment:
                    result = self.run_case(planner=planner)
                self.assertEqual(result["outcome"], "inconclusive")
                self.assertEqual(result["reason"], reason)
                self.assertIsNone(result["experiment"])
                experiment.assert_not_called()

    def test_extended_fixtures_isolate_one_setting_or_abstain(self):
        self.suite["cases"].extend(json.loads((FIXTURES / "investigation_cases.json").read_bytes()))
        for case_id, name, parameter in (
            ("refund-prompt", "restore_prompt", "prompt"),
            ("shipping-tool", "replay_tool_response", "tool_response"),
        ):
            with self.subTest(case=case_id):
                result = self.run_case(case_id)
                self.assertEqual(result["candidate"]["outcome"], "fail")
                self.assertEqual(result["outcome"], "supported")
                self.assertEqual(result["experiment"]["intervention"], name)
                self.assertEqual(result["experiment"]["config"], {
                    **result["candidate"]["config"], parameter: result["baseline"]["config"][parameter],
                })
                self.assertEqual(result["experiment"]["citation"], result["required_citation"])
        ambiguous = self.run_case("returns-ambiguous")
        self.assertEqual(ambiguous["candidate"]["outcome"], "fail")
        self.assertEqual(ambiguous["outcome"], "inconclusive")
        self.assertEqual(ambiguous["reason"], "planner_abstained")
        self.assertIsNone(ambiguous["experiment"])
