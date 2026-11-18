"""Apply a deterministic release policy to the versioned demo cases."""
import json
from pathlib import Path

if __package__:
    from .release_trace_demo import Config, digest, evaluate
else:
    from release_trace_demo import Config, digest, evaluate


def apply_gate(requirements, results, minimum_score):
    if not requirements or not 0 <= minimum_score <= 1:
        raise ValueError("A nonempty case policy and a threshold from 0 to 1 are required")
    missing = sorted(
        case_id for case_id in requirements
        if results.get(case_id, {}).get("outcome") not in {"pass", "fail"}
        or not results[case_id].get("evidence")
    )
    critical_failures = sorted(
        case_id for case_id, critical in requirements.items()
        if critical and case_id not in missing and results[case_id]["outcome"] == "fail"
    )
    passed = sum(
        results[case_id]["outcome"] == "pass"
        for case_id in requirements if case_id not in missing
    )
    score = passed / len(requirements)
    if critical_failures:
        verdict = "block"
    elif missing:
        verdict = "inconclusive"
    else:
        verdict = "pass" if score >= minimum_score else "block"
    return {
        "verdict": verdict,
        "score": score,
        "minimum_score": minimum_score,
        "critical_failures": critical_failures,
        "missing_evidence": missing,
    }


def evaluate_suite(fixture, suite, evaluate_variant):
    requirements = {
        item["case_id"]: item["critical"]
        for item in [fixture, *suite["cases"]]
    }
    cases = [fixture, *suite["cases"]]
    if len(requirements) != len(cases):
        raise ValueError("Case IDs must be unique")
    report = {"dataset_digest": digest(cases), "policy_digest": digest({
        "requirements": requirements, "minimum_score": suite["minimum_score"],
    })}
    for variant in ("baseline", "candidate"):
        results = {case["case_id"]: evaluate_variant(variant, case) for case in cases}
        report[variant] = {
            "results": results,
            **apply_gate(requirements, results, suite["minimum_score"]),
        }
    return report


def case_config(fixture, case, variant):
    return Config(**{**fixture[variant], **case.get(variant, {})})


def run_suite(fixture, suite):
    return evaluate_suite(fixture, suite, lambda variant, case: evaluate(case, case_config(fixture, case, variant)))


if __name__ == "__main__":
    directory = Path(__file__).parent
    fixture = json.loads((directory / "fixture.json").read_text())
    suite = json.loads((directory / "gate_suite.json").read_text())
    print(json.dumps(run_suite(fixture, suite), indent=2))
