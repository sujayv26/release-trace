"""Local HTTPS evaluation fixture with request counters and one interruptible response."""

import asyncio
from collections import Counter

PROJECTS = {
    "alpha": (5101, 6101, "refund-window", "refunds-v2", "How long is the refund window?"),
    "beta": (5102, 6102, "password-reset", "passwords-v3", "How do I reset my password?"),
}


def identity(project, variant):
    digit = {"alpha": {"baseline": "1", "candidate": "2"}, "beta": {"baseline": "3", "candidate": "4"}}
    value = digit[project][variant]
    return {"commit_sha": value * 40, "image_digest": "sha256:" + value * 64}


def main():
    import uvicorn
    from fastapi import FastAPI, HTTPException

    from demo.build_target import Question
    from service.evaluate_builds import Answer

    app = FastAPI()
    counts = Counter()
    hold_baseline = False

    @app.get("/requests")
    async def requests():
        return dict(counts)

    @app.post("/hold-next-alpha-baseline")
    async def hold_next():
        nonlocal hold_baseline
        hold_baseline = True
        return {"armed": True}

    @app.post("/{project}/{variant}/evaluate")
    async def evaluate(project: str, variant: str, question: Question) -> Answer:
        counts[f"{project}/{variant}"] += 1
        if project not in PROJECTS or variant not in {"baseline", "candidate", "unapproved"}:
            raise HTTPException(404, "Unknown fixture build")
        _, _, case_id, citation, expected_question = PROJECTS[project]
        if (question.case_id, question.question) != (case_id, expected_question):
            raise HTTPException(400, "Case does not belong to this fixture")
        nonlocal hold_baseline
        if hold_baseline and (project, variant) == ("alpha", "baseline"):
            hold_baseline = False
            await asyncio.sleep(15)
        return Answer(build=identity(project, "baseline" if variant == "baseline" else "candidate"), citation=citation)

    uvicorn.run(app, host="0.0.0.0", port=8443, ssl_certfile="/tls/cert.pem", ssl_keyfile="/tls/key.pem",
                access_log=False)


if __name__ == "__main__":
    main()
