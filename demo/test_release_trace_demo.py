import json
import unittest
from pathlib import Path

from release_trace_demo import Config, evaluate, retrieval_experiment


class RetrievalExperimentTest(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(Path(__file__).with_name("fixture.json").read_text())
        self.baseline = Config(**self.fixture["baseline"])
        self.candidate = Config(**self.fixture["candidate"])

    def test_retrieval_swap_restores_citation(self):
        self.assertEqual(evaluate(self.fixture, self.baseline)["outcome"], "pass")
        self.assertEqual(evaluate(self.fixture, self.candidate)["outcome"], "fail")
        candidate = Config("candidate-prompt", self.candidate.retrieval)
        result = retrieval_experiment(self.fixture, self.baseline, candidate, {})
        self.assertEqual(result["outcome"], "pass")
        self.assertEqual(result["config"], {"prompt": "candidate-prompt", "retrieval": "newest"})

    def test_repeat_reuses_result(self):
        results = {}
        first = retrieval_experiment(self.fixture, self.baseline, self.candidate, results)
        second = retrieval_experiment(self.fixture, self.baseline, self.candidate, results)
        self.assertIs(first, second)
        self.assertEqual(len(results), 1)

    def test_missing_evidence_is_inconclusive(self):
        self.fixture["policies"] = []
        self.assertEqual(evaluate(self.fixture, self.baseline)["outcome"], "inconclusive")
