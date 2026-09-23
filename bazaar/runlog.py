"""Per-run JSONL logs under ``runs/<run_id>/``.

Three files are written during a run: ``messages.jsonl`` (every frame sent and
received), ``decisions.jsonl`` (what the policy chose and why), and
``shadow.jsonl`` (what a shadow policy *would* have chosen).  One JSON object
per line, so a run can be replayed or grepped afterwards.

Records are built from ``model`` dataclasses rather than the generated
bindings, which keeps this file on the domain side of the wall.  Tokens live
only in connection headers and never appear in a message, so logging whole
messages is safe -- but headers are never logged.
"""

import json
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path


def jsonable(value):
    """Render a model object as plain JSON types, naming enums.

    Field names mirror the proto, so a logged message reads like the schema.
    Enums become their names (``"RESOURCE_WATER"``) rather than integers,
    because a log is for a human.
    """
    if is_dataclass(value):
        return {f.name: jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return value


class RunLog:
    """Appends JSON lines to one run's directory, creating it on first use."""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def write(self, filename: str, record: dict) -> None:
        line = json.dumps(jsonable(record), sort_keys=False)
        with (self.directory / filename).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def message(self, direction: str, message) -> None:
        """Log one frame.  ``direction`` is ``"sent"`` or ``"received"``."""
        self.write(
            "messages.jsonl",
            {"direction": direction, "kind": message.which(), "message": message.inner()},
        )
