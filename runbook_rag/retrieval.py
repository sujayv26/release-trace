import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).with_name("data")
TOKEN = re.compile(r"[a-z0-9]+")
STOP_WORDS = frozenset(
    "a am an and are before by can do does for from get how i in is it me my "
    "of on or our should the to use what when where which with would you".split()
)


@dataclass(frozen=True)
class Citation:
    id: str
    title: str
    excerpt: str


def keywords(text: str) -> set[str]:
    return set(TOKEN.findall(text.lower())) - STOP_WORDS


def read_corpus(corpus: Path) -> bytes:
    with corpus.open("rb") as stream:
        raw = stream.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Runbook corpus exceeds 2 MB")
    return raw


def build_index(database: Path, corpus: Path = DATA / "runbooks.json", *, stale_checkout=False,
                snapshot: bytes | None = None) -> None:
    records = json.loads(read_corpus(corpus) if snapshot is None else snapshot)
    if not isinstance(records, list) or not 1 <= len(records) <= 512:
        raise ValueError("Runbook corpus requires 1 to 512 passages")
    ids = set()
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise ValueError(f"Invalid runbook passage {index}")
        source, title, excerpt = (record.get(key) for key in ("id", "title", "excerpt"))
        if (not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", source)
                or source in ids or record.get("status") not in ("active", "superseded")
                or not isinstance(title, str) or not title.strip() or len(title) > 200
                or not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 3000):
            raise ValueError(f"Invalid runbook passage {index}")
        ids.add(source)
    if not any(record["status"] == "active" for record in records):
        raise ValueError("Runbook corpus requires at least one active passage")
    active = {record["id"] for record in records if record["status"] == "active"}
    if stale_checkout:
        active.remove("checkout-rollback-v2")
        active.add("checkout-rollback-v1")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE VIRTUAL TABLE runbooks USING fts5(id UNINDEXED, title, excerpt)"
        )
        connection.executemany(
            "INSERT INTO runbooks VALUES (?, ?, ?)",
            (
                (record["id"], record["title"], record["excerpt"])
                for record in records
                if record["id"] in active
            ),
        )


def search(database: Path, question: str, limit: int = 3) -> list[Citation]:
    terms = keywords(question)
    if len(terms) < 2:
        return []
    query = " OR ".join(f'"{term}"' for term in sorted(terms))
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as db:
        rows = db.execute(
            """
            SELECT id, title, excerpt FROM runbooks
            WHERE runbooks MATCH ?
            ORDER BY bm25(runbooks, 0.0, 2.0, 1.0), id
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    return [Citation(*row) for row in rows]


def answer(question: str, candidates: list[Citation]) -> dict:
    terms = keywords(question)
    for candidate in candidates:
        matched = terms & keywords(f"{candidate.title} {candidate.excerpt}")
        if len(matched) >= 2 and len(matched) * 2 >= len(terms):
            return {"status": "answered", "answer": candidate.excerpt, "citations": [candidate]}
    return {"status": "insufficient_evidence", "answer": None, "citations": []}
