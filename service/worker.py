"""Consume queued release evaluations."""

import argparse
import time

from .store import database, process_one


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one delivery")
    args = parser.parse_args()
    engine = database()
    try:
        while True:
            processed = process_one(engine)
            if args.once:
                return
            if processed is None:
                time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
