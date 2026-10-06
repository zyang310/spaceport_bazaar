"""Summarise logs that already exist, e.g. every run from before this was built.

    python -m bazaar.dashboard.history [PATH ...]      # default: runs/

Each PATH is a run directory, or a directory of them.  Existing summaries are
rewritten, so this is also how to pick up a change to the summary's shape.
"""

import argparse
import sys
from pathlib import Path

from .files import run_dirs, write_summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bazaar.dashboard.history", description=__doc__)
    parser.add_argument("paths", nargs="*", default=["runs"], help="run directories, or directories of them")
    args = parser.parse_args(argv)

    failed = done = 0
    for path in args.paths:
        if not Path(path).is_dir():
            print(f"not a directory: {path}")
            failed += 1
            continue
        for directory in run_dirs(path):
            try:
                print(write_summary(directory))
                done += 1
            except Exception as exc:
                print(f"{directory}: {type(exc).__name__}: {exc}")
                failed += 1
    print(f"{done} summarised, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
