"""Reading a run's log and writing its summary beside it.

The only place in ``history`` that touches the disk.  A run directory is
whatever holds a ``messages.jsonl``; the summary lands next to it as
``history.json`` and is always written whole-or-not-at-all, so a page reading
it while a run ends never sees half a file.
"""

import json
import os
from pathlib import Path

from .summary import summarize_session

MESSAGES_FILE = "messages.jsonl"
SUMMARY_FILE = "history.json"


def read_rows(path: Path) -> tuple[list[dict], int]:
    """``(rows, skipped)`` from a JSONL log.

    A run stopped with Ctrl+C can leave a half-written last line, and a summary
    is most wanted exactly then, so an unparseable line is counted and skipped
    rather than fatal.
    """
    rows, skipped = [], 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                skipped += 1
    return rows, skipped


def write_summary(run_dir: Path | str, *, context: dict | None = None) -> Path:
    """Summarise ``run_dir/messages.jsonl`` into ``run_dir/history.json``.

    A run that never got a message on the wire -- a refused connection, say --
    still gets a file, with no runs in it: the history should show that it was
    tried.  Returns the path written.
    """
    run_dir = Path(run_dir)
    log = run_dir / MESSAGES_FILE
    rows, skipped = read_rows(log) if log.exists() else ([], 0)
    summary = summarize_session(rows, session=run_dir.name, context=context, skipped_lines=skipped)

    target = run_dir / SUMMARY_FILE
    scratch = target.with_suffix(".json.tmp")
    scratch.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    os.replace(scratch, target)
    return target


def run_dirs(root: Path | str) -> list[Path]:
    """Every run directory at or directly under ``root``, oldest name first."""
    root = Path(root)
    if (root / MESSAGES_FILE).exists():
        return [root]
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / MESSAGES_FILE).exists())
