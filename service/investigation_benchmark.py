"""Measure local investigation methods on a versioned synthetic holdout."""

import json

from demo.release_gate import case_config, run_suite
from demo.release_trace_demo import INTERVENTIONS, digest

from .evaluate_builds import FIXTURES
from .investigation_graph import inspect_case, scripted_planner
from .investigator import investigate


def compare_case(fixture):
    case_id = fixture["case_id"]
    suite = {"minimum_score": 1, "cases": []}
    report = run_suite(fixture, suite)
    results = {name: report[name]["results"][case_id] for name in ("baseline", "candidate")}
    configs = [case_config(fixture, fixture, name) for name in ("baseline", "candidate")]
    choice = scripted_planner(inspect_case(*configs, results))
    finding = investigate({"fixture": fixture, "suite": suite}, report, case_id)
    experiment = finding["experiment"]
    return {
        "single_change_rule": {
            "diagnosis": None if choice == "finish" else choice,
            "reason": "no_unique_change" if choice == "finish" else "only_changed_setting",
            "experiments": 0,
        },
        "scripted_investigator": {
            "diagnosis": experiment["intervention"] if finding["outcome"] == "supported" else None,
            "reason": finding["reason"],
            "experiments": int(experiment is not None),
        },
    }


def summarize(cases, method):
    diagnosable = sum(case["expected"] is not None for case in cases)
    answers = [case for case in cases if case[method]["diagnosis"] is not None]
    correct = sum(case[method]["diagnosis"] == case["expected"] for case in answers)
    abstentions = len(cases) - len(answers)
    correct_abstentions = sum(case["expected"] is None and case[method]["diagnosis"] is None for case in cases)
    return {
        "cases": len(cases), "diagnosable": diagnosable, "answered": len(answers),
        "correct_diagnoses": correct, "incorrect_diagnoses": len(answers) - correct,
        "recall_on_diagnosable": correct / diagnosable if diagnosable else None,
        "accuracy_when_answered": correct / len(answers) if answers else None,
        "abstentions": abstentions, "correct_abstentions": correct_abstentions,
        "experiments": sum(case[method]["experiments"] for case in cases),
        "model_calls": 0,
    }


def benchmark(dataset):
    rows = []
    seen = set()
    for entry in dataset["cases"]:
        fixture = entry["fixture"]
        case_id = fixture["case_id"]
        if case_id in seen or entry["expected"] not in (None, *INTERVENTIONS):
            raise ValueError(f"Invalid holdout case or diagnosis label for {case_id}")
        seen.add(case_id)
        outcomes = compare_case(fixture)
        rows.append({"case_id": case_id, "expected": entry["expected"], **outcomes})
    return {
        "revision": dataset["revision"], "dataset_digest": digest(dataset),
        "methods": {method: summarize(rows, method) for method in ("single_change_rule", "scripted_investigator")},
        "cases": rows,
    }


if __name__ == "__main__":
    dataset = json.loads((FIXTURES / "investigation_holdout.json").read_bytes())
    print(json.dumps(benchmark(dataset), indent=2))
