"""Run the first ReleaseTrace experiment without external services."""

from dataclasses import dataclass, replace
from hashlib import sha256
from json import dumps, loads
from pathlib import Path


@dataclass(frozen=True)
class Config:
    prompt: str
    retrieval: str


def digest(value):
    return sha256(dumps(value, sort_keys=True).encode()).hexdigest()


def evaluate(fixture, config):
    words = set(fixture["question"].lower().rstrip("?").split())
    relevant = [
        policy for policy in fixture["policies"]
        if words & set(policy["text"].lower().split())
    ]
    select = {"newest": max, "oldest": min}[config.retrieval]
    chosen = select(relevant, key=lambda policy: policy["revision"], default=None)
    citation = chosen["id"] if chosen else None
    outcome = "inconclusive" if chosen is None else (
        "pass" if citation == fixture["required_citation"] else "fail"
    )
    return {
        "config": vars(config),
        "config_digest": digest(vars(config)),
        "citation": citation,
        "evidence": chosen,
        "outcome": outcome,
    }


def retrieval_experiment(fixture, baseline, candidate, results):
    intervention = replace(candidate, retrieval=baseline.retrieval)
    experiment_id = digest([fixture, vars(baseline), vars(candidate), "restore_retrieval"])
    if experiment_id not in results:
        results[experiment_id] = {
            "experiment_id": experiment_id,
            "intervention": "restore_retrieval",
            **evaluate(fixture, intervention),
        }
    return results[experiment_id]


def main():
    fixture = loads(Path(__file__).with_name("fixture.json").read_text())
    baseline = Config(**fixture["baseline"])
    candidate = Config(**fixture["candidate"])
    print(dumps({
        "case": fixture["case_id"],
        "fixture_digest": digest(fixture),
        "expected_citation": fixture["required_citation"],
        "baseline": evaluate(fixture, baseline),
        "candidate": evaluate(fixture, candidate),
        "experiment": retrieval_experiment(fixture, baseline, candidate, {}),
    }, indent=2))


if __name__ == "__main__":
    main()
