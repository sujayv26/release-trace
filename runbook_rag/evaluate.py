import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .retrieval import DATA, answer, build_index, search


def evaluate(database: Path) -> dict:
    cases = json.loads((DATA / "eval.json").read_text(encoding="utf-8"))
    results = []
    ranks = []
    for case in cases:
        question, expected = case["question"], case["citation"]
        candidates = search(database, question)
        ids = [candidate.id for candidate in candidates]
        response = answer(question, candidates)
        citations = response["citations"]
        cited_ids = [citation.id for citation in citations]
        if expected is None:
            correct = response["status"] == "insufficient_evidence" and not citations
            correct = correct and response["answer"] is None
        else:
            ranks.append(ids.index(expected) + 1 if expected in ids else 0)
            correct = response["status"] == "answered" and cited_ids == [expected]
            correct = correct and response["answer"] == citations[0].excerpt
        forbidden = set(case.get("forbidden", [])) & set(ids + cited_ids)
        results.append({"id": case["id"], "retrieved": ids, "cited": cited_ids,
                        "forbidden_found": sorted(forbidden), "passed": correct and not forbidden})
    return {
        "cases": results,
        "passed": sum(case["passed"] for case in results),
        "total": len(results),
        "recall_at_3": sum(rank > 0 for rank in ranks) / len(ranks),
        "mrr_at_3": sum(1 / rank for rank in ranks if rank) / len(ranks),
    }


def main() -> int:
    with TemporaryDirectory(prefix="runbook-eval-") as directory:
        database = Path(directory) / "runbooks.db"
        build_index(database)
        report = evaluate(database)
    print(json.dumps(report, indent=2))
    return int(report["passed"] != report["total"])


if __name__ == "__main__":
    raise SystemExit(main())
