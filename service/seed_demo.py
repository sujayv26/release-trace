"""Queue a signed synthetic push against a local ReleaseTrace API."""

import hmac
import json
import os
from urllib.request import Request, urlopen


def main():
    repo = int(os.environ["RELEASETRACE_REPOSITORY_ID"])
    installation = int(os.environ["RELEASETRACE_INSTALLATION_ID"])
    secret = os.environ["RELEASETRACE_WEBHOOK_SECRET"].encode()
    body = json.dumps({
        "after": "1" * 40,
        "ref": "refs/heads/demo",
        "deleted": False,
        "repository": {"id": repo},
        "installation": {"id": installation},
    }).encode()
    request = Request("http://127.0.0.1:8000/webhooks/github", data=body, headers={
        "Content-Type": "application/json",
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": f"demo-{repo}-{installation}",
        "X-Hub-Signature-256": "sha256=" + hmac.digest(secret, body, "sha256").hex(),
    })
    with urlopen(request, timeout=5) as response:
        print(response.read().decode())


if __name__ == "__main__":
    main()
