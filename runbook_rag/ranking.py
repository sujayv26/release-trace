"""Offline hybrid retrieval and lexical reranking for the runbook corpus."""

from contextlib import closing
from math import sqrt
from pathlib import Path
import sqlite3
from time import perf_counter

from .retrieval import Citation, answer, keywords, search

VARIANTS = ("bm25", "hybrid", "hybrid-reranked")
DEFAULT_VARIANT = "hybrid"

# Normalize common operational vocabulary before constructing trigram vectors.
ALIASES = {
    "undo": "rollback", "revert": "rollback", "reversal": "rollback",
    "rollout": "deployment", "deploy": "deployment",
    "piling": "backlog", "tasks": "job", "jobs": "job",
    "spreadsheet": "csv", "download": "export", "stuck": "stalled",
    "toggle": "flag", "signoff": "approval",
}


def concepts(text):
    return {ALIASES.get(term, term) for term in keywords(text)}


def trigrams(terms):
    return {word[index:index + 3] for term in terms
            for word in [f"_{term}_"] for index in range(len(word) - 2)}


def coverage(question, document):
    return len(question & document) / max(len(question), 1)


class RunbookSearch:
    def __init__(self, database: Path):
        self.database = database
        with closing(sqlite3.connect(database)) as connection:
            rows = connection.execute("SELECT id, title, excerpt FROM runbooks ORDER BY id").fetchall()
        self.documents = [Citation(*row) for row in rows]
        self.title_terms = {doc.id: concepts(doc.title) for doc in self.documents}
        self.document_terms = {doc.id: concepts(f"{doc.title} {doc.excerpt}") for doc in self.documents}
        self.vectors = {doc.id: trigrams(self.document_terms[doc.id]) for doc in self.documents}

    def run(self, question, variant=DEFAULT_VARIANT):
        if variant not in VARIANTS:
            raise ValueError("Unknown retrieval variant")
        started = perf_counter()
        candidates = search(self.database, question, limit=3 if variant == "bm25" else 5)
        query = concepts(question)
        if variant != "bm25":
            vector = trigrams(query)
            scores = {doc.id: len(vector & self.vectors[doc.id]) /
                      sqrt(max(1, len(vector) * len(self.vectors[doc.id]))) for doc in self.documents}
            semantic = sorted((doc for doc in self.documents if scores[doc.id] > 0),
                              key=lambda doc: (-scores[doc.id], doc.id))[:5]
            fused = {}
            for ranking in (candidates, semantic):
                for rank, doc in enumerate(ranking, 1):
                    fused[doc] = fused.get(doc, 0) + 1 / (10 + rank)
            candidates = sorted(fused, key=lambda doc: (-fused[doc], doc.id))
        retrieved = perf_counter()
        if variant == "hybrid-reranked":
            candidates.sort(key=lambda doc: (
                -(2 * coverage(query, self.title_terms[doc.id]) +
                  coverage(query, self.document_terms[doc.id])),
                doc.id,
            ))
        candidates = candidates[:3]
        ranked = perf_counter()
        if variant == "bm25":
            response = answer(question, candidates)
        else:
            selection = next((doc for doc in candidates
                              if len(query & self.title_terms[doc.id]) >= 2
                              and coverage(query, self.document_terms[doc.id]) >= 0.6), None)
            response = {
                "status": "answered" if selection else "insufficient_evidence",
                "answer": selection.excerpt if selection else None,
                "citations": [selection] if selection else [],
            }
        validated = perf_counter()
        timings = {"retrieval_ms": (retrieved - started) * 1000,
                   "citation_validation_ms": (validated - ranked) * 1000}
        if variant == "hybrid-reranked":
            timings["reranking_ms"] = (ranked - retrieved) * 1000
        return candidates, response, timings
