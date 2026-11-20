"""Build three companion revisions and run their local OCI digests on Docker's containerd image store."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
from secrets import token_hex
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic, sleep

import httpx

from .rehearse import rehearse


def check(*command, **kwargs):
    return subprocess.check_output(command, **kwargs).decode().strip()


def drill(checkout):
    refs = {"stable": "pilot", "stale-candidate": "build/stale-checkout",
            "corrected-candidate": "build/corrected-checkout"}
    token = token_hex(32)
    targets = {}
    with ExitStack() as stack:
        def container(port, image, *options, env=None):
            cid = check("docker", "run", "-d", "-p", f"127.0.0.1::{port}", *options, image, env=env)
            stack.callback(subprocess.run, ["docker", "rm", "-fv", cid], stdout=subprocess.DEVNULL, check=True)
            return check("docker", "port", cid, f"{port}/tcp")

        for role, ref in refs.items():
            commit = check("git", "-C", checkout, "rev-parse", "--verify", f"{ref}^{{commit}}")
            source = subprocess.check_output(["git", "-C", checkout, "archive", commit])
            tag = f"releasetrace-pilot-target:{commit}"
            check("docker", "build", "--label", f"org.opencontainers.image.revision={commit}",
                  "-f", "runbook_rag/Dockerfile", "-t", tag, "-", input=source)
            reference = check("docker", "image", "inspect", tag, "--format", "{{index .RepoDigests 0}}")
            digest = reference.rsplit("@", 1)[1]
            address = container(8000, reference, "--pull=never", "-e", "RUNBOOK_ACCESS_TOKEN", "-e", f"RUNBOOK_BUILD_COMMIT={commit}",
                                "-e", f"RUNBOOK_IMAGE_DIGEST={digest}", env={**os.environ, "RUNBOOK_ACCESS_TOKEN": token})
            url = f"http://{address}"
            deadline = monotonic() + 30
            with httpx.Client(timeout=2, trust_env=False) as client:
                while monotonic() < deadline:
                    try:
                        if client.get(url + "/health").is_success:
                            break
                    except httpx.TransportError:
                        pass
                    sleep(0.2)
                else:
                    raise RuntimeError(f"{role} failed readiness")
            targets[role] = {"url": url + "/evaluate", "build": {"commit_sha": commit, "image_digest": digest}}
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "targets.json"
            path.write_text(json.dumps(targets), encoding="utf-8")
            reports = rehearse(path, token)
        return {role: {"verdict": report["verdict"], "builds": report["builds"],
                       "citation": report["candidate"]["results"]["checkout-rollback"]["citation"]}
                for role, report in reports.items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", help="Companion checkout with local pilot and both build branches")
    print(json.dumps(drill(parser.parse_args().checkout), indent=2))
