"""What the dashboard's history tab is built from: one JSON summary per run.

``summary`` turns a run's message log into plain data; ``files`` reads the log
and writes ``history.json`` beside it.  Nothing here imports the rest of the
client, which is what lets this move out with the dashboard.
"""

from .files import MESSAGES_FILE, SUMMARY_FILE, run_dirs, write_summary
from .summary import SCHEMA_VERSION, summarize_session

__all__ = ["MESSAGES_FILE", "SCHEMA_VERSION", "SUMMARY_FILE", "run_dirs", "summarize_session", "write_summary"]
