"""Public entry points for each stage of reroll's wheel-to-repodata pipeline.

`reroll()` (`reroll.__init__`) is just `extract_metadata_file` ->
`parse_metadata` -> `get_wheel_records` in sequence. Importing from here
directly lets a caller run, replace, or skip a stage on its own -- e.g.
calling `get_wheel_records` on a `WheelMetadata` sourced from a database
instead of a real wheel file.
"""

from __future__ import annotations

from reroll.wheel_archive import extract_metadata_file
from reroll.wheel_metadata import parse_metadata
from reroll.wheel_record import get_wheel_records

__all__ = ["extract_metadata_file", "get_wheel_records", "parse_metadata"]
