"""Build an anonymous, offline walkthrough from the checked-in synthetic checkout case."""

import argparse
from html import escape
import json
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from demo.release_gate import evaluate_cases
from runbook_rag.ranking import RunbookSearch
from runbook_rag.retrieval import DATA, build_index


def page(title, content):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self'; form-action 'none'; base-uri 'none'">
<title>{title} · ReleaseTrace</title><link rel="stylesheet" href="styles.css"></head>
<body><header class="topbar"><div class="shell topbar-inner"><a class="brand" href="index.html">
<span class="brand-mark" aria-hidden="true">R</span>ReleaseTrace</a><strong>Synthetic demo data</strong></div></header>
<main class="shell content">{content}</main></body></html>"""


def build(output):
    # Publish only this known synthetic case. The export has no API client, account,
    # project selector, or database connection, even when run on an operator machine.
    spec = json.loads((DATA / "release-suite.json").read_bytes())
    case, = spec["suite"]["cases"]
    if case["case_id"] != "checkout-rollback" or spec["policy"]["critical_cases"] != [case["case_id"]]:
        raise ValueError("Expected the public checkout case")
    case["critical"] = True
    roles = ("stable", "stale-candidate", "corrected-candidate")
    citations = {}
    with TemporaryDirectory() as tmp:
        for role in roles:
            db = Path(tmp) / role
            build_index(db, stale_checkout=role == "stale-candidate")
            _, response, _ = RunbookSearch(db).run(case["question"])
            citation, = response["citations"]
            policy = next(p for p in case["policies"] if p["id"] == citation.id)
            if policy["text"] != citation.excerpt:
                raise ValueError("Retrieved excerpt differs from the versioned public policy")
            citations[role] = {"citation": citation.id, "evidence": policy,
                               "outcome": "pass" if citation.id == case["required_citation"] else "fail"}

    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Choose an empty publish directory")
    css = Path(__file__).resolve().parents[1] / "dashboard" / "app" / "styles.css"
    copyfile(css, output / "styles.css")
    rows = []
    for role, expected, title in (("stale-candidate", "block", "Blocked candidate"),
                                  ("corrected-candidate", "pass", "Corrected candidate")):
        report = evaluate_cases([case], spec["policy"]["minimum_score"],
                                lambda variant, _: citations["stable" if variant == "baseline" else role])
        verdict = report["candidate"]["verdict"]
        if report["baseline"]["verdict"] != "pass" or verdict != expected:
            raise ValueError("Public rehearsal produced an unexpected verdict")
        blocked = verdict == "block"
        status = "Blocked" if blocked else "Passed"
        reason = ("The candidate recommends an immediate rollback. The current runbook requires release-lead approval."
                  if blocked else "Both builds cite the current rollback procedure. The critical citation check passes.")
        filename = f"{role}.html"
        rows.append(f'<li><a class="run-row" href="{filename}"><strong>{title}</strong>'
                    f'<span class="status" data-status="{verdict}">{status}</span><span>Open →</span></a></li>')
        cards = []
        for variant, label in (("baseline", "Stable build"), ("candidate", "Proposed build")):
            item = report[variant]["results"][case["case_id"]]["evidence"]
            current = item["id"] == case["required_citation"]
            cards.append(f'<article class="panel result" data-variant="{variant}"><h2>{label}</h2>'
                         f'<p>{"Current" if current else "Superseded"} citation</p>'
                         f'<code>{escape(item["id"])}</code><blockquote>{escape(item["text"])}</blockquote></article>')
        other = ('<a href="corrected-candidate.html">Open corrected passing run →</a>' if blocked else
                 '<a href="stale-candidate.html">Revisit blocked run →</a>')
        content = f"""<a href="index.html">← Release history</a>
<section class="decision-hero" data-verdict="{verdict}"><div class="eyebrow">Checkout rollback · {status}</div>
<h1>{title}</h1><p>{reason}</p></section><h2>{escape(case["question"])}</h2>
<p>Required source for this critical case: <code>{escape(case["required_citation"])}</code></p>
<div class="comparison">{''.join(cards)}</div><p class="page-links">{other}</p>
<details class="panel review-guide"><summary>What did the gate measure?</summary>
<p>One critical case. The gate compares citation IDs against the expected policy revision.
Answer correctness needs a separate check. These runs replay synthetic retrieval locally.</p></details>"""
        (output / filename).write_text(page(title, content), encoding="utf-8")
    index = f"""<section class="hero"><div class="eyebrow">Runbook RAG · Release review</div>
<h1>Would you ship this rollback advice?</h1>
<p>Compare two proposed versions of a checkout assistant. Open the blocked run first to see
which instruction changed and why a reviewer would stop the release.</p></section>
<h2>Release history</h2><ul class="panel run-list">{''.join(rows)}</ul>"""
    (output / "index.html").write_text(page("Release history", index), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
