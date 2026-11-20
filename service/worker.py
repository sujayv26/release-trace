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
    parser.add_argument("--auxiliary-only", action="store_true", help="Leave evaluations to the Temporal worker")
    args = parser.parse_args()
    engine = database()
    with ExitStack() as stack:
        stack.callback(engine.dispose)
        apps = ()
        if args.publish_checks:
            from .check_queue import GitHubApp, github_client, publish_one

            try:
                apps = GitHubApp.configured()
            except (KeyError, TypeError, ValueError, OSError):
                parser.error("Could not load GitHub App publishing configuration. Check the IDs, project directory, and RSA key file.")
            client = stack.enter_context(github_client())
        while True:
            processed = None if args.auxiliary_only else process_one(engine)
            investigated = process_investigation(engine)
            published = False
            for app in apps:
                if publish_one(engine, app, client):
                    published = True
            if args.once:
                return
            if processed is None and investigated is None and not published:
                time.sleep(1)


if __name__ == "__main__":
    main()
