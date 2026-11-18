"""Preview or publish a check for a completed fixture evaluation."""

import argparse
import json
import os
import re

import httpx
from sqlalchemy import select

from .store import database, deliveries

CHECK_NAME = "ReleaseTrace / Synthetic evaluation"
REPOSITORY_NAME = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}")


def completed_run(engine, delivery_id, repository_id, installation_id):
    with engine.connect() as connection:
        row = connection.execute(select(
            deliveries.c.id, deliveries.c.commit_sha, deliveries.c.report,
        ).where(
            deliveries.c.id == delivery_id, deliveries.c.repository_id == repository_id,
            deliveries.c.installation_id == installation_id, deliveries.c.state == "completed",
        )).one_or_none()
    if row is None or row.report is None:
        raise ValueError("No completed evaluation was found in the configured project")
    return row


def check_payload(run):
    candidate = run.report["candidate"]
    summary = [
        "Results cover the synthetic fixture suite. The commit SHA identifies the push that triggered it.",
        f"Fixture gate: **{candidate['verdict']}**. Cases passed: **{candidate['score']:.0%}**.",
    ]
    if candidate["critical_failures"]:
        summary.append("Critical failures: " + ", ".join(candidate["critical_failures"]))
    if candidate["missing_evidence"]:
        summary.append("Cases missing evidence: " + ", ".join(candidate["missing_evidence"]))
    return {
        "name": CHECK_NAME,
        "head_sha": run.commit_sha,
        "external_id": run.id,
        "status": "completed",
        "conclusion": "neutral",
        "output": {"title": "Synthetic fixture evaluation", "summary": "\n\n".join(summary)},
    }


def publish_check(client, repository_name, repository_id, app_id, payload):
    if not REPOSITORY_NAME.fullmatch(repository_name) or repository_name.split("/")[1] in {".", ".."}:
        raise ValueError("Expected a GitHub repository in owner/name form")
    repository = f"/repos/{repository_name}"
    if client.get(repository).raise_for_status().json()["id"] != repository_id:
        raise ValueError("GitHub repository does not match the saved evaluation scope")

    # Reconcile a previous create before attempting a new one. The publisher
    # processes each delivery serially, including retries after timeouts.
    for page in range(1, 11):
        response = client.get(f"{repository}/commits/{payload['head_sha']}/check-runs", params={
            "app_id": app_id, "check_name": payload["name"], "filter": "all", "per_page": 100, "page": page,
        }).raise_for_status()
        checks = response.json()["check_runs"]
        for check in checks:
            if ((check.get("app") or {}).get("id") == app_id and check.get("name") == payload["name"]
                    and check.get("head_sha") == payload["head_sha"] and check.get("external_id") == payload["external_id"]):
                return {"status": "existing", "check_id": check["id"]}
        if "next" not in response.links:
            created = client.post(f"{repository}/check-runs", json=payload).raise_for_status().json()
            return {"status": "published", "check_id": created["id"]}
    raise ValueError("Could not reconcile existing GitHub checks within the pagination limit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delivery_id")
    parser.add_argument("--publish", action="store_true", help="Publish the neutral check to GitHub")
    args = parser.parse_args()
    try:
        repository_id = int(os.environ["RELEASETRACE_REPOSITORY_ID"])
        installation_id = int(os.environ["RELEASETRACE_INSTALLATION_ID"])
        engine = database()
        try:
            payload = check_payload(completed_run(engine, args.delivery_id, repository_id, installation_id))
        finally:
            engine.dispose()

        if not args.publish:
            print(json.dumps(payload, indent=2))
            return
        if args.delivery_id.startswith("demo-"):
            raise ValueError("The seeded demo uses a placeholder SHA. Publish from a real GitHub delivery.")
        app_id = int(os.environ["RELEASETRACE_GITHUB_APP_ID"])
        token = os.environ["RELEASETRACE_GITHUB_INSTALLATION_TOKEN"]
        with httpx.Client(base_url="https://api.github.com", timeout=10, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }) as client:
            result = publish_check(client, os.environ["RELEASETRACE_GITHUB_REPOSITORY"], repository_id, app_id, payload)
        print(json.dumps(result))
    except KeyError as exc:
        parser.exit(1, f"A required setting or report field is missing: {exc.args[0]}\n")
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    except httpx.HTTPStatusError as exc:
        parser.exit(1, f"GitHub returned HTTP {exc.response.status_code}.\n")
    except httpx.RequestError:
        parser.exit(1, "GitHub request failed. Retry to reconcile any previous publish.\n")


if __name__ == "__main__":
    main()
