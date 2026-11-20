"""Compare retrieval variants on the frozen synthetic holdout."""

import argparse
from hashlib import sha256
import json
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from .ranking import RunbookSearch, VARIANTS
from .retrieval import DATA, build_index


def benchmark(retriever, cases, variant, repeats):
    results, timings, ranks = [], [], []
    for case in cases:
        question, expected = case["question"], case["citation"]
        candidates, response, _ = retriever.run(question, variant)
        for _ in range(repeats):
            started = perf_counter()
            retriever.run(question, variant)
            timings.append((perf_counter() - started) * 1000)
        retrieved = [doc.id for doc in candidates]
        citation = next((doc.id for doc in response["citations"]), None)
        if expected is not None:
            ranks.append(retrieved.index(expected) + 1 if expected in retrieved else 0)
        results.append({"id": case["id"], "group": case["group"], "citation": citation,
                        "correct": citation == expected, "retrieved": retrieved,
                        "answerable": expected is not None})
    return {
        "recall_at_3": sum(rank > 0 for rank in ranks) / len(ranks),
        "mrr_at_3": sum(1 / rank for rank in ranks if rank) / len(ranks),
        "citation_correct": sum(row["correct"] and row["answerable"] for row in results),
        "answerable": len(ranks),
        "abstentions_correct": sum(row["correct"] and not row["answerable"] for row in results),
        "unanswerable": len(results) - len(ranks),
        "superseded_retrievals": sum("checkout-rollback-v1" in row["retrieved"] for row in results),
        "p95_ms": round(sorted(timings)[ceil(0.95 * len(timings)) - 1], 3),
        "cases": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=30)
    if (args := parser.parse_args()).repeats < 1:
        parser.error("Provide a positive repeat count")
    holdout = (DATA / "holdout.json").read_bytes()
    with TemporaryDirectory() as directory:
        database = Path(directory) / "holdout.db"
        build_index(database)
        retriever = RunbookSearch(database)
        report = {variant: benchmark(retriever, json.loads(holdout), variant, args.repeats)
                  for variant in VARIANTS}
    print(json.dumps({"holdout_sha256": sha256(holdout).hexdigest(), "variants": report}, indent=2))


if __name__ == "__main__":
    main()
