"""Queue a signed synthetic push against a local ReleaseTrace API."""

import argparse
import hmac
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from .evaluate_builds import BuildTargets


def queue_demo(targets_path=None, *, extended=False):
    repo = int(os.environ["RELEASETRACE_REPOSITORY_ID"])
    installation = int(os.environ["RELEASETRACE_INSTALLATION_ID"])
    secret = os.environ["RELEASETRACE_WEBHOOK_SECRET"].encode()
    sha = "1" * 40
    delivery_id = f"demo-{repo}-{installation}"
    if extended:
        delivery_id += "-extended"
    if targets_path:
        targets = BuildTargets.model_validate_json(targets_path.read_bytes())
        sha = targets.candidate.build.commit_sha
        delivery_id += f"-build-{sha}"
    body = json.dumps({
        "after": sha,
        "ref": "refs/heads/demo",
        "deleted": False,
        "repository": {"id": repo},
        "installation": {"id": installation},
    }).encode()
    request = Request("http://127.0.0.1:8000/webhooks/github", data=body, headers={
        "Content-Type": "application/json",
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": "sha256=" + hmac.digest(secret, body, "sha256").hex(),
    })
    with urlopen(request, timeout=5) as response:
        if response.status != 202:
            raise RuntimeError(f"Expected webhook HTTP 202, received {response.status}")
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", type=Path, help="Use the candidate SHA from the local build target file")
    parser.add_argument("--extended", action="store_true", help="Use a distinct delivery ID for the extended fixture suite")
    args = parser.parse_args()
    print(json.dumps(queue_demo(args.targets, extended=args.extended)))


if __name__ == "__main__":
    main()
