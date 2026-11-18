"""Consume queued release evaluations."""

import argparse
import time
from contextlib import ExitStack

from .investigator import process_investigation
from .store import database, process_one


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run one pass through the queues")
    parser.add_argument("--publish-checks", action="store_true", help="Publish completed build checks to GitHub")
    args = parser.parse_args()
    engine = database()
    with ExitStack() as stack:
        stack.callback(engine.dispose)
        app = None
        if args.publish_checks:
            from .check_queue import GitHubApp, github_client, publish_one

            try:
                app = GitHubApp.from_environment()
            except (KeyError, TypeError, ValueError, OSError):
                parser.error("Could not load the GitHub App IDs, repository, or RSA key file")
            client = stack.enter_context(github_client())
        while True:
            processed = process_one(engine)
            investigated = process_investigation(engine)
            published = app is not None and publish_one(engine, app, client)
            if args.once:
                return
            if processed is None and investigated is None and not published:
                time.sleep(1)


if __name__ == "__main__":
    main()
