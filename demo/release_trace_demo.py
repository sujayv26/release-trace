"""Run the first ReleaseTrace experiment without external services."""

from dataclasses import dataclass, replace
from hashlib import sha256
from json import dumps, loads
from pathlib import Path


@dataclass(frozen=True)
class Config:
    prompt: str
    retrieval: str
    tool_response: str = ""

    def snapshot(self):
        return {key: value for key, value in vars(self).items() if key != "tool_response" or value}


INTERVENTIONS = {
    "restore_retrieval": "retrieval",
    "restore_prompt": "prompt",
    "replay_tool_response": "tool_response",
}


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
    override = fixture.get("citation_by_setting")
    if override:
        expected = override["citations"].get(getattr(config, override["setting"]))
        chosen = next((policy for policy in fixture["policies"] if policy["id"] == expected), None)
    citation = chosen["id"] if chosen else None
    outcome = "inconclusive" if chosen is None else (
        "pass" if citation == fixture["required_citation"] else "fail"
    )
    snapshot = config.snapshot()
    return {
        "config": snapshot,
        "config_digest": digest(snapshot),
        "citation": citation,
        "evidence": chosen,
        "outcome": outcome,
    }


def retrieval_experiment(fixture, baseline, candidate, results):
    return controlled_experiment(fixture, baseline, candidate, results, "restore_retrieval")


def controlled_experiment(fixture, baseline, candidate, results, name):
    setting = INTERVENTIONS[name]
    intervention = replace(candidate, **{setting: getattr(baseline, setting)})
    experiment_id = digest([fixture, baseline.snapshot(), candidate.snapshot(), name])
    if experiment_id not in results:
        results[experiment_id] = {
            "experiment_id": experiment_id,
            "intervention": name,
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
